# Storage Auditor & Safe Mover

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%2F%2011-lightgrey.svg)](https://www.microsoft.com/windows/)
[![UI](https://img.shields.io/badge/GUI-CustomTkinter-darkgreen.svg)](https://customtkinter.tomschimansky.com/)
[![Tests](https://img.shields.io/badge/tests-pytest-brightgreen.svg)](https://docs.pytest.org/)

A high-performance, non-destructive desktop audit and file synchronization engine designed for Windows. It provides intelligent deduplication, heuristic game and savegame mapping, triaged action planning, and cryptographic verification with staging copy safeguards.

---

## 🌐 Language Navigation / Navegação de Idioma
- [🇺🇸 English Documentation](#-english-documentation)
  - [Overview & Core Principles](#overview--core-principles)
  - [Key Features](#key-features)
  - [Safety & Non-Destructive Guarantee](#safety--non-destructive-guarantee)
  - [Project Architecture](#project-architecture)
  - [Requirements & Installation](#requirements--installation)
  - [Usage Guide](#usage-guide)
  - [Automated Testing](#automated-testing)
  - [Roadmap](#roadmap)
- [🇧🇷 Documentação em Português](#-documentação-em-português)
  - [Visão Geral e Princípios](#visão-geral-e-princípios)
  - [Funcionalidades Principais](#funcionalidades-principais)
  - [Garantia de Segurança Não-Destrutiva](#garantia-de-segurança-não-destrutiva)
  - [Arquitetura do Projeto](#arquitetura-do-projeto)
  - [Requisitos e Instalação](#requisitos-e-instalação)
  - [Guia de Uso](#guia-de-uso)
  - [Testes Automatizados](#testes-automatizados)
  - [Roadmap](#roadmap-1)

---

# 🇺🇸 English Documentation

## Overview & Core Principles

**Storage Auditor & Safe Mover** was engineered to solve storage saturation on high-density Windows workstations (typically constrained SSD OS drives vs. secondary storage drives) without risking accidental data loss.

The architecture strictly decouples analysis from execution:
1. **Engine 1 (Auditor & Reporter - Read-Only):** Scans storage roots using high-speed NTFS metadata traversals, computes hierarchical fingerprints, maps games and saves, detects duplicates, and exports structured reports (JSON and Excel). It **never modifies, renames, or deletes any file**.
2. **Engine 2 (Safe Mover - Cryptographic Executor):** Executes strictly approved Action Plans. By default, it operates in **Copy-Only mode**. It prepares staging copies in temporary target locations, computes dual SHA-256 hashes during I/O, verifies byte counts and modification times, and commits files via atomic rename only upon cryptographic confirmation.

---

## Key Features

- **Multi-Stage Fast Fingerprinting:**
  - *Stage 0 (Zero-Filter):* Discards system and transient files (`.tmp`, `.log`, `.sys`, `.dll`, shader caches).
  - *Stage 1 (Exact Size Grouping):* Direct metadata byte grouping via `os.scandir` without redundant stat overhead.
  - *Stage 2 (Collision Sampling):* Reads first and last 64 KB chunks to eliminate non-matching duplicates with minimal I/O.
  - *Stage 3 (Full Cryptographic Hash):* Computes complete SHA-256 in 1 MB blocks only for sampled collisions.
- **Relational Cross-Drive Comparator:** Compares candidate roots across primary (e.g., `C:\`) and secondary (e.g., `D:\`) drives to detect verified duplicate files and pending backups.
- **Game & Savegame Mapping:** Identifies installations from Steam (`libraryfolders.vdf`, `appmanifest_*.acf`), Epic Games, standalone libraries, and Microsoft Store/Xbox packages via PowerShell appx queries. Maps associated savegame directories located in `%USERPROFILE%\Documents`, `Saved Games`, `%APPDATA%`, and `%LOCALAPPDATA%`.
- **Responsive CustomTkinter Desktop GUI:** Non-blocking background worker threads with thread-safe queue updates, real-time KPI metrics, paginated data inspection, and dark mode interface.
- **Hierarchical Triage Tree:** Fast in-memory triage management supporting folder-level inclusion, exclusion, and review decisions with inherited propagation.
- **Windows Extended Long-Path Support (`\\?\`):** Transparent extended path normalization ensuring compatibility with deep hierarchies exceeding the Windows `MAX_PATH` (260-character) limitation.
- **Append-Only Execution Logging:** Generates comprehensive JSONL logs recording every preflight validation, staging step, byte transfer, duration, and status code.

---

## Safety & Non-Destructive Guarantee

> [!IMPORTANT]
> **Strict Non-Destructive Policy:**
> - The software is **copy-only by default**.
> - Source files are treated as strictly **read-only**. No source file is moved, overwritten, renamed, or deleted automatically.
> - Destination files are written to isolated temporary files (`.~tmp_safecopy_*.tmp`) in the destination directory and validated against the source hash before final placement.
> - If a destination file already exists, the operation halts that order as a conflict to avoid accidental overwrites.
> - Any physical file removal from source locations requires separate manual confirmation and an explicit user directive.

---

## Project Architecture

```text
storage_auditor/
├── config.py              # Global settings, extensions, and portable path resolvers
├── main.py                # Command-line interface (CLI) entry point (Read-Only Engine 1)
├── gui_launcher.py        # Desktop graphical user interface (GUI) launcher
├── requirements.txt       # Python dependencies
├── core/                  # Core processing engine
│   ├── scanner.py         # NTFS directory scanning engine via os.scandir
│   ├── fingerprint.py     # 4-stage tiered hashing algorithms
│   ├── comparator.py      # Cross-drive relational deduplication and matching
│   ├── cache.py           # SQLite incremental hash cache
│   ├── game_mapper.py     # Heuristic library and savegame resolver
│   ├── windows_apps.py    # Safe PowerShell wrapper for Microsoft Store packages
│   ├── triage.py          # In-memory hierarchy decision manager
│   ├── action_plan.py     # Action Plan schema, preflight, and capacity calculator
│   ├── safe_mover.py      # Safe Copy executor with staging and dual-hash validation
│   └── path_utils.py      # Windows extended path (\\?\) helper utilities
├── exporters/             # Report generators
│   ├── json_exporter.py   # Structured JSON reports exporter
│   └── excel_exporter.py  # Formatted multi-tab Excel (.xlsx) workbook generator
├── gui/                   # Desktop GUI layer (CustomTkinter)
│   ├── app.py             # Main application window and layout
│   ├── worker.py          # Background audit worker thread
│   ├── data_loader.py     # Report cache and data loading utility
│   └── views/             # Tab views (Summary, Duplicates, Pending, Games, Safe Mover)
├── tests/                 # Automated test suite (pytest)
└── docs/                  # Architecture and engineering documentation
```

---

## Requirements & Installation

### Requirements
- **Operating System:** Windows 10 or Windows 11 (64-bit)
- **Python Version:** Python 3.10 or higher

### Installation
1. Clone the repository:
   ```powershell
   git clone https://github.com/your-username/storage-auditor.git
   cd storage-auditor
   ```
2. Create and activate a virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
3. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```

---

## Usage Guide

### 1. Graphical User Interface (Recommended)
Launch the desktop application:
```powershell
python gui_launcher.py
```
*(or `python main.py --gui`)*

- Select source user folders (Documents, Downloads, Desktop, etc.).
- Configure secondary drive roots (e.g., `D:\`) and game directories.
- Click **"Iniciar Auditoria Completa"** to scan without altering files.
- Inspect categorized findings across the tabs.
- Review and approve action plans in the **Safe Mover** tab.

### 2. Command-Line Interface (Headless Mode)
Run a read-only audit using default discovered roots:
```powershell
python main.py
```
Run with custom parameters:
```powershell
python main.py `
  --c-roots "C:\Users\Username\Documents" "C:\Users\Username\Downloads" `
  --d-roots "D:\" `
  --game-roots "D:\Games" "D:\SteamLibrary" `
  --output-dir "reports" `
  --full-hash-limit-mb 100
```

---

## Automated Testing

Execute the comprehensive test suite:
```powershell
pytest -v
```
The test suite covers:
- Directory scanning and NTFS reparse point handling
- Fast fingerprint multi-tier hash collisions
- Relational comparator edge cases
- Action plan generation, space margin estimations, and serialization
- Safe Mover atomic staging, failure handling, and retry logic
- Extended path handling exceeding 260 characters

---

## Roadmap

- [ ] Linux and macOS filesystem scanning support
- [ ] Direct cloud storage synchronization target providers (S3, B2)
- [ ] Exportable interactive HTML audit dashboard
- [ ] Real-time filesystem watcher for incremental triage updates

---

## License

This project is licensed under the terms of the [MIT License](LICENSE). See [LICENSE](LICENSE) for details.

---

# 🇧🇷 Documentação em Português

## Visão Geral e Princípios

O **Storage Auditor & Safe Mover** foi projetado para resolver a saturação de armazenamento em computadores Windows com unidades de alta densidade técnica (como SSD principal do sistema operacional vs. unidades secundárias de armazenamento), eliminando o risco de perda acidental de dados.

A arquitetura estabelece uma separação rigorosa entre análise e execução:
1. **Motor 1 (Auditor & Reporter — Somente Leitura):** Varre as pastas raízes por meio de travessias de metadados NTFS em alta velocidade, calcula impressões digitais hierárquicas, mapeia bibliotecas de jogos e saves, detecta duplicatas e exporta relatórios estruturados (JSON e Excel). **Nunca altera, renomeia ou apaga arquivos**.
2. **Motor 2 (Safe Mover — Executor Criptográfico):** Executa ordens estritamente contidas em um Plano de Ação aprovado. Por padrão, opera em **modo somente-cópia**. Utiliza diretório temporário de destino (`.~tmp_safecopy_*.tmp`), calcula hash SHA-256 duplo durante a transferência de bytes, verifica tamanho e data de modificação, e efetua a renomeação atômica apenas após confirmação criptográfica de integridade.

---

## Funcionalidades Principais

- **Hashing em 4 Estágios (Fast Fingerprint):**
  - *Estágio 0 (Filtro Zero):* Descarte de arquivos transitórios e de sistema (`.tmp`, `.log`, `.sys`, `.dll`, caches de shaders).
  - *Estágio 1 (Agrupamento por Tamanho Exato):* Agrupamento direto de bytes via `os.scandir` sem sobrecarga de chamadas redundantes a `os.stat`.
  - *Estágio 2 (Amostragem em Colisão):* Leitura rápida dos primeiros 64 KB e últimos 64 KB para eliminar falsos positivos de mesmo tamanho com mínimo impacto em disco.
  - *Estágio 3 (Hash Criptográfico Completo):* Cálculo do SHA-256 integral em blocos de 1 MB exclusivamente para arquivos com colisão amostral confirmada.
- **Cruzamento Relacional entre Discos:** Compara raízes do disco primário (`C:\`) e secundário (`D:\`), identificando duplicatas confirmadas e pendências de backup.
- **Mapeamento de Jogos e Saves:** Detecta instalações da Steam (`libraryfolders.vdf`, `appmanifest_*.acf`), Epic Games, pastas avulsas e pacotes Microsoft Store/Xbox via PowerShell. Mapeia pastas de saves em `%USERPROFILE%\Documents`, `Saved Games`, `%APPDATA%` e `%LOCALAPPDATA%`.
- **Interface Desktop CustomTkinter:** Execução não-bloqueante em thread secundária, atualização contínua de status, paginação de grandes volumes de dados e suporte a tema escuro.
- **Triagem Hierárquica em Árvore:** Gestão de decisões em memória com inclusão, exclusão e revisão em lote com herança hierárquica automática.
- **Suporte a Caminhos Longos no Windows (`\\?\`):** Tratamento transparente de caminhos estendidos que excedem o limite clássico de 260 caracteres do Windows (`MAX_PATH`).
- **Log Append-Only Estruturado (JSONL):** Registro imutável de cada pré-validação, transferência temporária, duração, contagem de bytes e confirmação de hash.

---

## Garantia de Segurança Não-Destrutiva

> [!IMPORTANT]
> **Política Estrita de Não-Destruição:**
> - O sistema opera em **modo somente-cópia por padrão**.
> - Todas as origens são estritamente **somente-leitura**. Nenhum arquivo de origem é movido, renomeado, alterado ou apagado por conta própria.
> - Arquivos de destino são escritos em arquivos temporários isolados no diretório alvo e validados bit a bit antes da renomeação final.
> - Se o arquivo de destino já existir, a operação é interrompida como conflito para evitar qualquer sobrescrita acidental.
> - Qualquer movimentação física ou remoção da origem exige autorização humana prévia e comando explícito do usuário.

---

## Arquitetura do Projeto

```text
storage_auditor/
├── config.py              # Configurações globais, extensões e resolução portável de caminhos
├── main.py                # Ponto de entrada CLI (Motor 1 de Auditoria Read-Only)
├── gui_launcher.py        # Ponto de entrada amigável para a Interface Desktop (GUI)
├── requirements.txt       # Declaração de dependências do Python
├── core/                  # Núcleo de processamento
│   ├── scanner.py         # Motor de varredura NTFS via os.scandir
│   ├── fingerprint.py     # Algoritmos de hashing em múltiplos estágios
│   ├── comparator.py      # Comparador relacional e detecção de duplicatas
│   ├── cache.py           # Cache incremental SQLite para hashes
│   ├── game_mapper.py     # Mapeamento heurístico de jogos e saves
│   ├── windows_apps.py    # Wrapper PowerShell seguro para pacotes MS Store
│   ├── triage.py          # Gerenciador em memória da árvore de triagem
│   ├── action_plan.py     # Esquema de Plano de Ação e estimativa de espaço
│   ├── safe_mover.py      # Executor de Cópia Segura com staging e hash dual SHA-256
│   └── path_utils.py      # Utilitário de caminhos estendidos do Windows (\\?\)
├── exporters/             # Exportadores de relatórios
│   ├── json_exporter.py   # Exportador de relatórios estruturados JSON
│   └── excel_exporter.py  # Gerador de planilhas formatadas em abas Excel (.xlsx)
├── gui/                   # Camada de interface gráfica (CustomTkinter)
│   ├── app.py             # Janela principal e componentes visuais
│   ├── worker.py          # Thread de execução em segundo plano
│   ├── data_loader.py     # Carregamento e cache dos relatórios
│   └── views/             # Abas visuais (Resumo, Duplicatas, Pendentes, Jogos, Safe Mover)
├── tests/                 # Suíte de testes automatizados (pytest)
└── docs/                  # Documentação de arquitetura e especificações
```

---

## Requisitos e Instalação

### Requisitos
- **Sistema Operacional:** Windows 10 ou Windows 11 (64-bit)
- **Versão do Python:** Python 3.10 ou superior

### Instalação
1. Clone o repositório:
   ```powershell
   git clone https://github.com/seu-usuario/storage-auditor.git
   cd storage-auditor
   ```
2. Crie e ative o ambiente virtual:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
3. Instale as dependências:
   ```powershell
   pip install -r requirements.txt
   ```

---

## Guia de Uso

### 1. Interface Desktop (Modo Recomendado)
Execute o lançador da interface gráfica:
```powershell
python gui_launcher.py
```
*(ou `python main.py --gui`)*

- Selecione as pastas do usuário a auditar (Documentos, Downloads, etc.).
- Ajuste as unidades secundárias (ex.: `D:\`) e bibliotecas de jogos.
- Clique em **"Iniciar Auditoria Completa"** para varrer o disco sem alterar nenhum arquivo.
- Navegue pelas 5 abas paginadas para inspecionar os resultados.
- Visualize o plano de ação na aba **Safe Mover** e execute cópias de forma controlada.

### 2. Linha de Comando (Modo Headless)
Execute a auditoria padrão somente-leitura:
```powershell
python main.py
```
Com parâmetros customizados:
```powershell
python main.py `
  --c-roots "C:\Users\Username\Documents" "C:\Users\Username\Downloads" `
  --d-roots "D:\" `
  --game-roots "D:\Games" "D:\SteamLibrary" `
  --output-dir "reports" `
  --full-hash-limit-mb 100
```

---

## Testes Automatizados

Para executar os testes unitários e de integração:
```powershell
pytest -v
```
A suíte valida:
- Varredura de diretórios e descarte seguro de links simbólicos/reparse points
- Algoritmo escalonado de impressões digitais contra colisões parciais
- Cruzamento relacional entre discos
- Construção, cálculo de folga de segurança e validação do Action Plan
- Staging temporário atômico, logs e isolamento do Safe Mover
- Suporte a caminhos longos acima de 260 caracteres (`\\?\`)

---

## Roadmap

- [ ] Suporte a varredura e cópia segura em ambientes Linux e macOS
- [ ] Sincronização direta com provedores de armazenamento em nuvem (S3, B2)
- [ ] Exportação de dashboard interativo em HTML para relatórios corporativos
- [ ] Monitoramento em tempo real do sistema de arquivos para triagem incremental

---

## Licença

Este projeto é licenciado sob os termos da licença [MIT](LICENSE). Consulte o arquivo [LICENSE](LICENSE) para obter detalhes completos.
