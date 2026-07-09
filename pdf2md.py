import os
import re
import argparse
from PIL import Image
import io
import pdfplumber
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), ".env"))

class PDF2MDConverter:
    def __init__(self, pdf_path, output_md_path, extract_imgs=True, use_llm=True, api_key=None, api_url=None, model=None):
        self.pdf_path = pdf_path
        self.output_md_path = output_md_path
        self.extract_imgs = extract_imgs
        self.use_llm = use_llm
        self.output_dir = os.path.dirname(output_md_path)
        self.assets_dir = os.path.join(self.output_dir, "_Assets")
        
        # Einstellungen mit Fallback auf .env
        self.api_key = api_key or os.getenv("API_KEY_DEEPSEEK")
        self.api_url = api_url or "https://api.deepseek.com/v1/chat/completions"
        self.model = model or "deepseek-chat"
        
        if self.extract_imgs and not os.path.exists(self.assets_dir):
            os.makedirs(self.assets_dir)

    def clean_text(self, text):
        if not text:
            return ""
        # Silbentrennung am Zeilenende entfernen
        text = re.sub(r'(\w+)-\n(\w+)', r'\1\2', text)
        return text

    def clean_header_footer(self, page):
        """
        Generische, positionsbasierte Kopf- und Fußzeilen-Filterung.
        Entfernt Textelemente, die sich im oberen (Kopf) oder unteren (Fuß) 
        Randbereich der Seite befinden.
        """
        header_margin = 75
        footer_margin = 50
        page_height = page.height
        
        # Filter nach Position
        words = page.extract_words()
        if not words:
            return ""
            
        # Gruppiere verbleibende Worte nach Zeilen (y-Koordinate)
        lines = {}
        for w in words:
            top = w["top"]
            bottom = w["bottom"]
            
            # Überspringe Kopf- und Fußzeilen
            if top < header_margin or bottom > (page_height - footer_margin):
                continue
                
            # Gruppiere Worte mit ähnlicher Vertikalposition auf eine Zeile
            # Wir runden die top-Koordinate, um leichte Schwankungen abzufangen
            y_key = round(top, 1)
            found_line = False
            for y in lines:
                if abs(y - y_key) < 3.0: # Toleranz für dieselbe Zeile
                    lines[y].append(w)
                    found_line = True
                    break
            if not found_line:
                lines[y_key] = [w]
                
        # Rekonstruiere den Text zeilenweise von oben nach unten
        sorted_y = sorted(lines.keys())
        reconstructed_lines = []
        
        for y in sorted_y:
            # Sortiere Wörter in der Zeile von links nach rechts (x0)
            sorted_words = sorted(lines[y], key=lambda x: x["x0"])
            line_text = " ".join([w["text"] for w in sorted_words])
            reconstructed_lines.append(line_text)
            
        return "\n".join(reconstructed_lines)

    def extract_font_styles(self, char_list):
        """Analysiert Zeichen-Fontstyles zur Kennzeichnung von fett/kursiv."""
        if not char_list:
            return []
        
        styled_text = []
        current_text = ""
        current_style = "normal" # normal, bold, italic, bold_italic

        for c in char_list:
            char_text = c["text"]
            font_name = c.get("fontname", "").lower()
            
            # Bestimme Style
            is_bold = "bold" in font_name or "black" in font_name
            is_italic = "italic" in font_name or "oblique" in font_name
            
            style = "normal"
            if is_bold and is_italic:
                style = "bold_italic"
            elif is_bold:
                style = "bold"
            elif is_italic:
                style = "italic"

            if style == current_style:
                current_text += char_text
            else:
                styled_text.append((current_text, current_style))
                current_text = char_text
                current_style = style
        
        if current_text:
            styled_text.append((current_text, current_style))

        # Formatieren zu Markdown
        md_text = ""
        for txt, style in styled_text:
            if not txt.strip():
                md_text += txt
                continue
            
            lead_ws = re.match(r'^(\s*)', txt).group(1)
            trail_ws = re.search(r'(\s*)$', txt).group(1)
            clean_txt = txt.strip()

            if style == "bold":
                md_text += f"{lead_ws}**{clean_txt}**{trail_ws}"
            elif style == "italic":
                md_text += f"{lead_ws}*{clean_txt}*{trail_ws}"
            elif style == "bold_italic":
                md_text += f"{lead_ws}***{clean_txt}***{trail_ws}"
            else:
                md_text += txt
        
        return md_text

    def format_table(self, table_data):
        """Konvertiert Roh-Tabellendaten in Markdown-Tabellen."""
        if not table_data:
            return ""
        
        md_lines = []
        # Header
        headers = [str(x).replace('\n', '<br>').strip() if x is not None else "" for x in table_data[0]]
        md_lines.append("| " + " | ".join(headers) + " |")
        md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        
        # Datenzeilen
        for row in table_data[1:]:
            row_cells = [str(x).replace('\n', '<br>').strip() if x is not None else "" for x in row]
            md_lines.append("| " + " | ".join(row_cells) + " |")
            
        return "\n".join(md_lines) + "\n\n"

    def convert(self):
        markdown_content = []
        headings_map = {} # Kapitelnummer (z. B. "1.1") -> Vollständiger Überschriften-Text
        
        # Durchlauf 1: Überschriften sammeln
        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                text = self.clean_header_footer(page)
                if not text:
                    continue
                text = self.clean_text(text)
                for line in text.split('\n'):
                    line_strip = line.strip()
                    # Kapitel-Erkennung (echte Überschrift, kein TOC)
                    if re.match(r'^\d+(\.\d+)*\s+\w', line_strip):
                        is_toc_line = "..." in line_strip or re.search(r'\.\s*\.\s*\.\s*\d+$', line_strip) or re.search(r'\.\s*\.\s*\.\s*$', line_strip) or re.search(r'\.\s+\d+$', line_strip)
                        if not is_toc_line:
                            match = re.match(r'^(\d+(?:\.\d+)*)\s+(.*)$', line_strip)
                            if match:
                                num = match.group(1)
                                title = match.group(2).strip()
                                headings_map[num] = f"{num} {title}"

        # Durchlauf 2: Konvertieren und Links setzen
        with pdfplumber.open(self.pdf_path) as pdf:
            print(f"Starte Konvertierung von: {self.pdf_path} ({len(pdf.pages)} Seiten)")
            
            # Um Fließtext über Seitengrenzen hinweg zu verschmelzen, 
            # sammeln wir zuerst alle Textblöcke und Tabellen über alle Seiten
            all_lines = []
            
            for page_idx, page in enumerate(pdf.pages):
                print(f"Lese Seite {page_idx + 1} von {len(pdf.pages)}...")
                import sys; sys.stdout.flush()
                
                # Extrahiere Tabellen auf dieser Seite und packe sie als spezielle Elemente in all_lines
                tables = page.extract_tables()
                table_texts = []
                for t in tables:
                    table_texts.append(self.format_table(t))
                
                text = self.clean_header_footer(page)
                if not text:
                    if table_texts:
                        for table_md in table_texts:
                            all_lines.append(f"__TABLE_START__\n{table_md}__TABLE_END__")
                    continue
                  
                text = self.clean_text(text)
                page_lines = text.split('\n')
                
                for line in page_lines:
                    l_str = line.strip()
                    if not l_str:
                        continue
                    
                    # Wenn das letzte Element in all_lines nicht mit Satzzeichen endet,
                    # und die aktuelle Zeile kein Strukturelement/Headline/Aufzählung ist,
                    # mergen wir direkt, um Seitenübergänge nahtlos zu machen.
                    if all_lines:
                        last_line = all_lines[-1].strip()
                        is_struct = re.match(r'^(\d+\)|[A-Z]\)|\(\d+\))\s*', l_str) or re.match(r'^\d+(\.\d+)*\s+[A-Z\u00c0-\u00d6]', l_str) or l_str.startswith('•') or l_str.startswith('-') or l_str.startswith('o ')
                        ends_punc = last_line and last_line[-1] in ".!?\":"
                        
                        if not ends_punc and not is_struct and not last_line.startswith("__TABLE_START__") and not last_line.startswith("#"):
                            all_lines[-1] = all_lines[-1] + " " + line
                            continue
                            
                    all_lines.append(line)
                
                if table_texts:
                    for table_md in table_texts:
                        all_lines.append(f"__TABLE_START__\n{table_md}__TABLE_END__")
            
            # Parser-Schleife über alle gesammelten Zeilen
            formatted_blocks = []
            current_paragraph = []
            in_list = False
            
            # Regex für Strukturelemente wie: 1), A), (10), (01)
            struct_regex = r'^(\d+\)|[A-Z]\)|\(\d+\))\s*'
            
            for line_idx, line in enumerate(all_lines):
                line_strip = line.strip()
                if not line_strip:
                    continue
                
                # Prüfe auf Tabellenplatzhalter
                if line_strip.startswith("__TABLE_START__"):
                    if current_paragraph:
                        formatted_blocks.append(" ".join(current_paragraph))
                        current_paragraph = []
                    table_content = line.replace("__TABLE_START__\n", "").replace("__TABLE_END__", "")
                    formatted_blocks.append(table_content)
                    in_list = False
                    continue

                # Kapitel-Erkennung (z.B. "1 Ziel der...", "1.1 Auftraggeber")
                if re.match(r'^\d+(\.\d+)*\s+[A-Z\u00c0-\u00d6]', line_strip):
                    if current_paragraph:
                        formatted_blocks.append(" ".join(current_paragraph))
                        current_paragraph = []
                        
                    is_toc_line = "..." in line_strip or re.search(r'\.\s*\.\s*\.\s*\d+$', line_strip) or re.search(r'\.\s*\.\s*\.\s*$', line_strip) or re.search(r'\.\s+\d+$', line_strip)
                    dots = line_strip.split(' ')[0].count('.')
                    
                    if is_toc_line:
                        match_num = re.match(r'^(\d+(?:\.\d+)*)', line_strip)
                        link_str = line_strip
                        if match_num and match_num.group(1) in headings_map:
                            target_heading = headings_map[match_num.group(1)]
                            clean_label = re.sub(r'\s*\.\s*\.\s*\d+$', '', line_strip)
                            clean_label = re.sub(r'\s*\.\s*\.\s*$', '', clean_label)
                            clean_label = re.sub(r'\s*\.\s*\d+$', '', clean_label)
                            link_str = f"[[#{target_heading}|{clean_label.strip()}]]"
                        
                        indent = "    " * dots
                        formatted_blocks.append(f"{indent}{link_str}")
                    else:
                        level = min(6, dots + 2) # 1 -> ##, 1.1 -> ###
                        formatted_blocks.append(f"{'#' * level} {line_strip}")
                    in_list = False
                    
                # Strukturelemente am Zeilenanfang (z.B. 1), A), (10), (01))
                elif re.match(struct_regex, line_strip):
                    if current_paragraph:
                        formatted_blocks.append(" ".join(current_paragraph))
                        current_paragraph = []
                    formatted_blocks.append(line_strip)
                    in_list = False
                    
                # Aufzählungszeichen (Listen)
                elif line_strip.startswith('•') or line_strip.startswith('-') or line_strip.startswith('o '):
                    if current_paragraph:
                        formatted_blocks.append(" ".join(current_paragraph))
                        current_paragraph = []
                    clean_item = re.sub(r'^[•\-o]\s*', '', line_strip)
                    # Wir speichern Listen-Items separat, markiert mit einem Präfix
                    formatted_blocks.append(f"__LIST_ITEM__- {clean_item}")
                    in_list = True
                    
                # Normaler Text (Fließtext)
                else:
                    if in_list:
                        in_list = False
                    
                    # Logik zur intelligenten Fließtext-Zusammenführung
                    if current_paragraph:
                        prev_line = current_paragraph[-1].strip()
                        starts_upper = line_strip[0].isupper() if line_strip else False
                        ends_punctuation = prev_line[-1] in ".!?\"" if prev_line else False
                        
                        # Ein neuer Absatz startet NUR, wenn die vorherige Zeile mit einem Satzzeichen endete
                        # UND die neue Zeile mit einem Großbuchstaben beginnt.
                        # Wenn die neue Zeile kleingeschrieben ist (z. B. nach Seitenumbruch), MUSS gemergt werden.
                        if ends_punctuation and starts_upper:
                            # Startet einen neuen Absatz
                            formatted_blocks.append(" ".join(current_paragraph))
                            current_paragraph = [line_strip]
                        elif not starts_upper:
                            # Kleingeschriebener Satzteil -> zwingend mergen
                            current_paragraph.append(line_strip)
                        else:
                            # Großgeschrieben, aber vorher kein Satzende -> ebenfalls mergen (z.B. Nomen am Zeilenanfang)
                            current_paragraph.append(line_strip)
                    else:
                        current_paragraph.append(line_strip)
    def query_deepseek(self, text_content):
        """Sendet Textblöcke zur semantischen Textglättung an das konfigurierte LLM."""
        if not self.api_key:
            print("Warnung: API-Key nicht gefunden. Führe Standard-Konvertierung aus.")
            return text_content
            
        print(f"Sende Text zur semantischen Textglättung an LLM ({self.model})...")
        import sys; sys.stdout.flush()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        system_prompt = (
            "Du bist ein PDF-zu-Markdown-Textglätter. Deine einzige Aufgabe ist es, den bereitgestellten Text "
            "semantisch zu glätten. Korrigiere Worttrennungen am Zeilenende, behebe fehlerhafte Leerzeichen, "
            "optimiere den Satzbau und stelle sicher, dass Formeln in LaTeX und Tabellen in sauberem Markdown "
            "formatiert sind. Verändere NIEMALS den inhaltlichen Sinn des Textes und füge keine Kommentare hinzu. "
            "Gib AUSSCHLIESSLICH das bereinigte Markdown zurück."
        )
        
        data = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text_content}
            ],
            "temperature": 0.1
        }
        
        try:
            response = requests.post(self.api_url, json=data, headers=headers, timeout=60)
            if response.status_code == 200:
                result = response.json()
                return result["choices"][0]["message"]["content"]
            else:
                print(f"Fehler bei LLM-API ({response.status_code}): {response.text}")
                return text_content
        except Exception as e:
            print(f"Fehler bei API-Aufruf: {e}")
            return text_content

    def convert(self):
        markdown_content = []
        headings_map = {} # Kapitelnummer (z. B. "1.1") -> Vollständiger Überschriften-Text
        
        # Durchlauf 1: Überschriften sammeln
        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                text = self.clean_header_footer(page)
                if not text:
                    continue
                text = self.clean_text(text)
                for line in text.split('\n'):
                    line_strip = line.strip()
                    # Kapitel-Erkennung (echte Überschrift, kein TOC)
                    if re.match(r'^\d+(\.\d+)*\s+\w', line_strip):
                        is_toc_line = "..." in line_strip or re.search(r'\.\s*\.\s*\.\s*\d+$', line_strip) or re.search(r'\.\s*\.\s*\.\s*$', line_strip) or re.search(r'\.\s+\d+$', line_strip)
                        if not is_toc_line:
                            match = re.match(r'^(\d+(?:\.\d+)*)\s+(.*)$', line_strip)
                            if match:
                                num = match.group(1)
                                title = match.group(2).strip()
                                headings_map[num] = f"{num} {title}"

        # Durchlauf 2: Konvertieren und Links setzen
        with pdfplumber.open(self.pdf_path) as pdf:
            print(f"Starte Konvertierung von: {self.pdf_path} ({len(pdf.pages)} Seiten)")
            
            # Um Fließtext über Seitengrenzen hinweg zu verschmelzen, 
            # sammeln wir zuerst alle Textblöcke und Tabellen über alle Seiten
            all_lines = []
            
            for page_idx, page in enumerate(pdf.pages):
                # Extrahiere Tabellen auf dieser Seite und packe sie als spezielle Elemente in all_lines
                tables = page.extract_tables()
                table_texts = []
                for t in tables:
                    table_texts.append(self.format_table(t))
                
                text = self.clean_header_footer(page)
                if not text:
                    if table_texts:
                        for table_md in table_texts:
                            all_lines.append(f"__TABLE_START__\n{table_md}__TABLE_END__")
                    continue
                  
                text = self.clean_text(text)
                page_lines = text.split('\n')
                
                for line in page_lines:
                    l_str = line.strip()
                    if not l_str:
                        continue
                    
                    # Wenn das letzte Element in all_lines nicht mit Satzzeichen endet,
                    # und die aktuelle Zeile kein Strukturelement/Headline/Aufzählung ist,
                    # mergen wir direkt, um Seitenübergänge nahtlos zu machen.
                    if all_lines:
                        last_line = all_lines[-1].strip()
                        is_struct = re.match(r'^(\d+\)|[A-Z]\)|\(\d+\))\s*', l_str) or re.match(r'^\d+(\.\d+)*\s+[A-Z\u00c0-\u00d6]', l_str) or l_str.startswith('•') or l_str.startswith('-') or l_str.startswith('o ')
                        ends_punc = last_line and last_line[-1] in ".!?\":"
                        
                        if not ends_punc and not is_struct and not last_line.startswith("__TABLE_START__") and not last_line.startswith("#"):
                            all_lines[-1] = all_lines[-1] + " " + line
                            continue
                            
                    all_lines.append(line)
                
                if table_texts:
                    for table_md in table_texts:
                        all_lines.append(f"__TABLE_START__\n{table_md}__TABLE_END__")
            
            # Parser-Schleife über alle gesammelten Zeilen
            formatted_blocks = []
            current_paragraph = []
            in_list = False
            
            # Regex für Strukturelemente wie: 1), A), (10), (01)
            struct_regex = r'^(\d+\)|[A-Z]\)|\(\d+\))\s*'
            
            for line_idx, line in enumerate(all_lines):
                line_strip = line.strip()
                if not line_strip:
                    continue
                
                # Prüfe auf Tabellenplatzhalter
                if line_strip.startswith("__TABLE_START__"):
                    if current_paragraph:
                        formatted_blocks.append(" ".join(current_paragraph))
                        current_paragraph = []
                    table_content = line.replace("__TABLE_START__\n", "").replace("__TABLE_END__", "")
                    formatted_blocks.append(table_content)
                    in_list = False
                    continue

                # Kapitel-Erkennung (z.B. "1 Ziel der...", "1.1 Auftraggeber")
                if re.match(r'^\d+(\.\d+)*\s+[A-Z\u00c0-\u00d6]', line_strip):
                    if current_paragraph:
                        formatted_blocks.append(" ".join(current_paragraph))
                        current_paragraph = []
                        
                    is_toc_line = "..." in line_strip or re.search(r'\.\s*\.\s*\.\s*\d+$', line_strip) or re.search(r'\.\s*\.\s*\.\s*$', line_strip) or re.search(r'\.\s+\d+$', line_strip)
                    dots = line_strip.split(' ')[0].count('.')
                    
                    if is_toc_line:
                        match_num = re.match(r'^(\d+(?:\.\d+)*)', line_strip)
                        link_str = line_strip
                        if match_num and match_num.group(1) in headings_map:
                            target_heading = headings_map[match_num.group(1)]
                            clean_label = re.sub(r'\s*\.\s*\.\s*\d+$', '', line_strip)
                            clean_label = re.sub(r'\s*\.\s*\.\s*$', '', clean_label)
                            clean_label = re.sub(r'\s*\.\s*\d+$', '', clean_label)
                            link_str = f"[[#{target_heading}|{clean_label.strip()}]]"
                        
                        indent = "    " * dots
                        formatted_blocks.append(f"{indent}{link_str}")
                    else:
                        level = min(6, dots + 2) # 1 -> ##, 1.1 -> ###
                        formatted_blocks.append(f"{'#' * level} {line_strip}")
                    in_list = False
                    
                # Strukturelemente am Zeilenanfang (z.B. 1), A), (10), (01))
                elif re.match(struct_regex, line_strip):
                    if current_paragraph:
                        formatted_blocks.append(" ".join(current_paragraph))
                        current_paragraph = []
                    formatted_blocks.append(line_strip)
                    in_list = False
                    
                # Aufzählungszeichen (Listen)
                elif line_strip.startswith('•') or line_strip.startswith('-') or line_strip.startswith('o '):
                    if current_paragraph:
                        formatted_blocks.append(" ".join(current_paragraph))
                        current_paragraph = []
                    clean_item = re.sub(r'^[•\-o]\s*', '', line_strip)
                    # Wir speichern Listen-Items separat, markiert mit einem Präfix
                    formatted_blocks.append(f"__LIST_ITEM__- {clean_item}")
                    in_list = True
                    
                # Normaler Text (Fließtext)
                else:
                    if in_list:
                        in_list = False
                    
                    # Logik zur intelligenten Fließtext-Zusammenführung
                    if current_paragraph:
                        prev_line = current_paragraph[-1].strip()
                        starts_upper = line_strip[0].isupper() if line_strip else False
                        ends_punctuation = prev_line[-1] in ".!?\"" if prev_line else False
                        
                        # Ein neuer Absatz startet NUR, wenn die vorherige Zeile mit einem Satzzeichen endete
                        # UND die neue Zeile mit einem Großbuchstaben beginnt.
                        # Wenn die neue Zeile kleingeschrieben ist (z. B. nach Seitenumbruch), MUSS gemergt werden.
                        if ends_punctuation and starts_upper:
                            # Startet einen neuen Absatz
                            formatted_blocks.append(" ".join(current_paragraph))
                            current_paragraph = [line_strip]
                        elif not starts_upper:
                            # Kleingeschriebener Satzteil -> zwingend mergen
                            current_paragraph.append(line_strip)
                        else:
                            # Großgeschrieben, aber vorher kein Satzende -> ebenfalls mergen (z.B. Nomen am Zeilenanfang)
                            current_paragraph.append(line_strip)
                    else:
                        current_paragraph.append(line_strip)
            
            if current_paragraph:
                formatted_blocks.append(" ".join(current_paragraph))
            
            # Ausgabeaufbereitung: Echte Absätze durch doppelte Zeilenumbrüche trennen
            final_markdown = []
            current_list = []
            
            for block in formatted_blocks:
                b_strip = block.strip()
                if not b_strip:
                    continue
                
                if b_strip.startswith("__LIST_ITEM__"):
                    item = b_strip.replace("__LIST_ITEM__", "")
                    current_list.append(item)
                else:
                    # Falls eine Liste aktiv war, jetzt wegschreiben
                    if current_list:
                        final_markdown.append("\n".join(current_list))
                        current_list = []
                    final_markdown.append(b_strip)
                    
            if current_list:
                final_markdown.append("\n".join(current_list))

        markdown_text = "\n\n".join(final_markdown)

        # Wenn LLM erwünscht ist, senden wir den Text an DeepSeek
        if self.use_llm:
            markdown_text = self.query_deepseek(markdown_text)

        # Schreiben der Markdown-Datei
        with open(self.output_md_path, 'w', encoding='utf-8') as f:
            f.write(markdown_text)
        print(f"Konvertierung abgeschlossen: {self.output_md_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PDF to Markdown Converter")
    parser.add_argument("--input", required=True, help="Pfad zur PDF-Datei")
    parser.add_argument("--output", required=True, help="Pfad zur Ziel-Markdown-Datei")
    parser.add_argument("--no-llm", action="store_true", help="Deaktiviert die DeepSeek API Textglättung")
    parser.add_argument("--api-key", default=None, help="Eigener API-Key für das LLM")
    parser.add_argument("--api-url", default=None, help="Eigene API-Basis-URL für das LLM")
    parser.add_argument("--model", default=None, help="Modell-Identifizierer für das LLM")
    args = parser.parse_args()
    
    use_llm = not args.no_llm
    converter = PDF2MDConverter(
        args.input, 
        args.output, 
        use_llm=use_llm, 
        api_key=args.api_key, 
        api_url=args.api_url, 
        model=args.model
    )
    converter.convert()
