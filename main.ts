import { Plugin, TFile, Notice, FileSystemAdapter, PluginSettingTab, Setting, App } from 'obsidian';
import { spawn } from 'child_process';
import * as path from 'path';

interface PDFIngestSettings {
    pythonPath: string;
    scriptPath: string;
    useLlm: boolean;
    apiUrl: string;
    apiKey: string;
    model: string;
}

const DEFAULT_SETTINGS: PDFIngestSettings = {
    pythonPath: 'python',
    scriptPath: 'C:/Users/Breti/AI_Workspace/Skills/pdf2md-importer/scripts/pdf2md.py',
    useLlm: false,
    apiUrl: 'https://api.deepseek.com/v1/chat/completions',
    apiKey: '',
    model: 'deepseek-chat'
}

export default class PDFIngestPlugin extends Plugin {
    settings!: PDFIngestSettings;

    async onload() {
        console.log('PDF Ingest Plugin geladen');
        await this.loadSettings();

        // Füge Einstellungsseite hinzu
        this.addSettingTab(new PDFIngestSettingTab(this.app, this));

        // Registriere Kontextmenü für PDF-Dateien
        this.registerEvent(
            this.app.workspace.on('file-menu', (menu, file) => {
                if (file instanceof TFile && file.extension === 'pdf') {
                    menu.addItem((item) => {
                        item
                            .setTitle('PDF-Ingest')
                            .setIcon('document')
                            .onClick(async () => {
                                await this.runPdfIngest(file);
                            });
                    });
                }
            })
        );
    }

    async loadSettings() {
        const loadedData = await this.loadData();
        const defaultData = Object.assign({}, DEFAULT_SETTINGS);
        
        // Verwende den lokalen Pfad nur, wenn in den geladenen Daten kein scriptPath existiert
        if (!loadedData || !loadedData.scriptPath) {
            const adapter = this.app.vault.adapter;
            if (adapter instanceof FileSystemAdapter) {
                const vaultPath = adapter.getBasePath();
                const localScriptPath = path.join(
                    vaultPath, 
                    this.app.vault.configDir, 
                    'plugins', 
                    'obsidian-pdf-ingest', 
                    'pdf2md.py'
                ).replace(/\\/g, '/');
                defaultData.scriptPath = localScriptPath;
            }
        }

        this.settings = Object.assign({}, defaultData, loadedData);
    }

    async saveSettings() {
        await this.saveData(this.settings);
    }

    async runPdfIngest(file: TFile) {
        const adapter = this.app.vault.adapter;
        if (!(adapter instanceof FileSystemAdapter)) {
            new Notice('Fehler: Dateisystem-Adapter nicht verfügbar.');
            return;
        }

        const absolutePdfPath = adapter.getFullPath(file.path);
        const parsedPath = path.parse(absolutePdfPath);
        let targetDir = parsedPath.dir;
        
        if (parsedPath.dir.endsWith('_Assets')) {
            targetDir = path.dirname(parsedPath.dir);
        }
        
        const absoluteMdPath = path.join(targetDir, `${parsedPath.name}.md`);
        
        const progressNotice = new Notice(`PDF-Ingest gestartet: ${parsedPath.name}\nInitialisiere Parser...`, 0);

        // Baue Argumente zusammen
        const args = [this.settings.scriptPath, '--input', absolutePdfPath, '--output', absoluteMdPath];
        
        if (!this.settings.useLlm) {
            args.push('--no-llm');
        } else {
            // Übergib API Parameter falls konfiguriert
            if (this.settings.apiKey) {
                args.push('--api-key', this.settings.apiKey);
            }
            if (this.settings.apiUrl) {
                args.push('--api-url', this.settings.apiUrl);
            }
            if (this.settings.model) {
                args.push('--model', this.settings.model);
            }
        }
        
        // Verwende konfigurierten Python-Pfad
        const process = spawn(this.settings.pythonPath, args);
        
        let lastOutput = '';

        process.stdout.on('data', (data) => {
            const output = data.toString().trim();
            if (output) {
                lastOutput = output;
                progressNotice.setMessage(`PDF-Ingest: ${parsedPath.name}\n➔ ${output}`);
                console.log(`[PDF-Ingest] stdout: ${output}`);
            }
        });

        process.stderr.on('data', (data) => {
            const errOutput = data.toString().trim();
            console.warn(`[PDF-Ingest] stderr: ${errOutput}`);
        });

        process.on('close', async (code) => {
            progressNotice.hide();

            if (code === 0) {
                new Notice(`PDF-Ingest erfolgreich abgeschlossen!`);
                
                let vaultRelativePath = `${file.basename}.md`;
                if (file.path.includes('_Assets/')) {
                    const parentPath = file.parent?.parent?.path || '';
                    vaultRelativePath = parentPath ? `${parentPath}/${file.basename}.md` : `${file.basename}.md`;
                } else {
                    vaultRelativePath = file.parent?.path ? `${file.parent.path}/${file.basename}.md` : `${file.basename}.md`;
                }

                setTimeout(async () => {
                    const newFile = this.app.vault.getAbstractFileByPath(vaultRelativePath);
                    if (newFile instanceof TFile) {
                        await this.app.workspace.getLeaf().openFile(newFile);
                    } else {
                        this.app.vault.trigger('create');
                    }
                }, 1000);
            } else {
                new Notice(`Fehler beim PDF-Ingest (Code ${code}). Letzte Meldung:\n${lastOutput}`);
            }
        });
    }

    onunload() {
        console.log('PDF Ingest Plugin entladen');
    }
}

class PDFIngestSettingTab extends PluginSettingTab {
    plugin: PDFIngestPlugin;

    constructor(app: App, plugin: PDFIngestPlugin) {
        super(app, plugin);
        this.plugin = plugin;
    }

    display(): void {
        const { containerEl } = this;
        containerEl.empty();
        containerEl.createEl('h2', { text: 'PDF-Ingest Einstellungen' });

        new Setting(containerEl)
            .setName('Python-Pfad')
            .setDesc('Der Befehl oder Pfad zur Python-Exekutive auf deinem System (z. B. "python" oder "python3").')
            .addText(text => text
                .setPlaceholder('python')
                .setValue(this.plugin.settings.pythonPath)
                .onChange(async (value) => {
                    this.plugin.settings.pythonPath = value;
                    await this.plugin.saveSettings();
                }));

        new Setting(containerEl)
            .setName('Skript-Pfad')
            .setDesc('Der absolute Pfad zur pdf2md.py im Gemmi-Workspace.')
            .addText(text => text
                .setPlaceholder('C:/Users/Breti/AI_Workspace/Skills/pdf2md-importer/scripts/pdf2md.py')
                .setValue(this.plugin.settings.scriptPath)
                .onChange(async (value) => {
                    this.plugin.settings.scriptPath = value;
                    await this.plugin.saveSettings();
                }));

        new Setting(containerEl)
            .setName('LLM-Textglättung nutzen')
            .setDesc('Verwendet generative KI (OpenAI-kompatible API) zur Nachbereitung und Format-Glättung des Textes.')
            .addToggle(toggle => toggle
                .setValue(this.plugin.settings.useLlm)
                .onChange(async (value) => {
                    this.plugin.settings.useLlm = value;
                    await this.plugin.saveSettings();
                    this.display(); // Aktualisiere Sichtbarkeit der restlichen Felder
                }));

        if (this.plugin.settings.useLlm) {
            containerEl.createEl('h3', { text: 'API Konfiguration' });
            
            const infoBox = containerEl.createDiv({ cls: 'setting-item-description' });
            infoBox.style.border = '1px solid var(--background-modifier-border)';
            infoBox.style.padding = '10px';
            infoBox.style.borderRadius = '4px';
            infoBox.style.marginBottom = '15px';
            infoBox.style.backgroundColor = 'var(--background-secondary)';
            infoBox.innerHTML = `
                <strong>Kurzanleitung zur API-Anbindung (Beispiel DeepSeek):</strong><br>
                <ul>
                    <li><strong>API-Basis-URL:</strong> Verwende den OpenAI-kompatiblen Chat-Endpunkt des Anbieters. Für DeepSeek lautet dieser: <br><code>https://api.deepseek.com/v1/chat/completions</code></li>
                    <li><strong>Modell:</strong> Nutze <code>deepseek-chat</code> für das Standard-Modell (DeepSeek-V3).</li>
                    <li><strong>API-Key:</strong> Dein persönlicher API-Key von DeepSeek (beginnt meist mit <code>sk-</code>).</li>
                </ul>
                <em>Hinweis:</em> Es können auch lokale Provider (z. B. LM Studio mit <code>http://localhost:1234/v1/chat/completions</code> und beliebigem Modellnamen) eingetragen werden.
            `;

            new Setting(containerEl)
                .setName('API-Basis-URL')
                .setDesc('Die vollständige URL zum Chat-Endpoint des Providers.')
                .addText(text => text
                    .setPlaceholder('https://api.deepseek.com/v1/chat/completions')
                    .setValue(this.plugin.settings.apiUrl)
                    .onChange(async (value) => {
                        this.plugin.settings.apiUrl = value;
                        await this.plugin.saveSettings();
                    }));

            new Setting(containerEl)
                .setName('API-Key')
                .setDesc('Dein API Token für den Provider.')
                .addText(text => {
                    text.inputEl.type = 'password';
                    text.setPlaceholder('sk-...')
                        .setValue(this.plugin.settings.apiKey)
                        .onChange(async (value) => {
                            this.plugin.settings.apiKey = value;
                            await this.plugin.saveSettings();
                        });
                });

            new Setting(containerEl)
                .setName('Modell')
                .setDesc('Der Modell-Identifizierer, der an die API gesendet wird.')
                .addText(text => text
                    .setPlaceholder('deepseek-chat')
                    .setValue(this.plugin.settings.model)
                    .onChange(async (value) => {
                        this.plugin.settings.model = value;
                        await this.plugin.saveSettings();
                    }));
        }
    }
}
