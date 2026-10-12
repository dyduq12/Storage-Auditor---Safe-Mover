# Storage Auditor & Safe Mover — Fase 3 (v0.3.0)

Motor de auditoria, desduplicação, inventário de jogos e movimentação segura de arquivos entre SSD C: e D:, equipado com **Interface Desktop CustomTkinter**, **Exportador Excel (.xlsx)** e **Safe Mover (Cópia Segura Não-Destrutiva)**.

---

## 🛡️ Aviso de Segurança e Integridade
- **Motor 1 (Auditor & Reporter):** opera **exclusivamente em modo somente leitura**. Nenhum arquivo pessoal do usuário é alterado, movido, renomeado ou excluído.
- **Motor 2 (Safe Mover - Fase 3):** opera em **modo estritamente não-destrutivo**. Realiza cópia controlada por plano de ação aprovado, com validação de hash SHA-256 no destino e **preservação 100% intacta da origem**. Não realiza exclusão, desduplicação física ou limpeza de pastas.

---

## 🚀 Como Executar

### 1. Pré-requisitos e Instalação
- Windows 10/11 com Python 3.10.6+ instalado.
- Instale as dependências no ambiente virtual:
```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Dependências principais:
- `customtkinter>=5.2.0` (Interface gráfica desktop moderna com tema escuro)
- `openpyxl>=3.1.0` (Exportação profissional de planilhas Excel)
- `pytest>=7.0.0` (Suíte de testes automatizados)

---

### 2. Interface Desktop CustomTkinter (GUI)

Inicie a interface gráfica diretamente com qualquer um dos comandos:
```powershell
.venv\Scripts\python gui_launcher.py
```
ou
```powershell
.venv\Scripts\python main.py --gui
```

#### Recursos da Interface Desktop:
- **Seleção de Escopo Visual:** Checkboxes ativadas por padrão para pastas padrão do usuário (`Documents`, `Downloads`, `Desktop`, `Videos`, `Pictures`), seletor nativo para adicionar pastas personalizadas, e campos editáveis para raízes de `D:\`, bibliotecas de jogos e diretório de saída.
- **Execução não-bloqueante (Thread-Safe):** A auditoria roda em background (`AuditWorker`) com comunicação via fila (`queue.Queue`). A janela nunca congela, exibindo barra de progresso e status detalhado em tempo real.
- **Cancelamento Limpo:** Botão "⏹ Cancelar" para interrupção graciosa a qualquer momento sem corromper o cache SQLite.
- **Visualização Paginada em 5 Abas:**
  1. **Resumo:** Cards executivos com totais em disco, arquivos indexados, espaço recuperável, jogos e saves. Botões para abrir a pasta `reports/`, recarregar relatórios existentes e exportar para Excel.
  2. **Duplicatas Confirmadas:** Tabela paginada (100 itens/página) cobrindo os 31.550+ registros, com busca textual, filtro de nível de confiança e badges visuais coloridos (`COMPLETO` em verde vs `AMOSTRAL - REVISAR` em laranja).
  3. **Pendentes de Backup:** Tabela paginada dos 83.404+ itens a sincronizar com D:, com filtro por extensão e checkbox de imagens `.iso`.
  4. **Documentos Avulsos:** Tabela dos 925 documentos prioritários com filtro por prioridade (`ALTA` / `MÉDIA`) e pasta de origem.
  5. **Jogos e Saves:** Inventário dos jogos mapeados em D: e tabela detalhada de saves em C:, com banner de advertência enfático e marcação de **"Vínculo Heurístico — Revisar"** para pastas compartilhadas de emuladores (`Public\Documents\Steam`) e casamentos por termos genéricos (`Edition`, `light`, `Tom Clancy`, mods).

---

### 3. Exportação para Excel (.xlsx)

Gera uma pasta de trabalho profissional consolidando as 5 abas com:
- Congelamento da primeira linha (`freeze_panes = "A2"`)
- Auto-filtros nativos do Excel em todas as colunas
- Cabeçalhos estilizados em azul institucional (`#1F4E78`) com texto em negrito
- Formatação numérica em bytes e unidades legíveis (GB, MB, KB)
- Sinalização explícita de duplicatas com confiança amostral e saves heurísticos

Pode ser disparada diretamente pela GUI (botão *"📊 Exportar Relatório Excel"*) ou programaticamente:
```python
from exporters.excel_exporter import ExcelReportExporter
exporter = ExcelReportExporter(output_dir="reports")
exporter.export_from_reports_dir()
```

---

### 4. Execução via Linha de Comando (CLI Headless)

A CLI da Fase 1 permanece 100% funcional e inalterada:
```powershell
.venv\Scripts\python main.py
```
Com parâmetros customizados:
```powershell
.venv\Scripts\python main.py `
  --c-roots "C:\Users\Eduardo\Documents" "C:\Users\Eduardo\Downloads" `
  --d-roots "D:\" `
  --game-roots "D:\Games" "D:\SteamLibrary" `
  --output-dir "reports" `
  --full-hash-limit-mb 100
```

---

### 5. Execução dos Testes Automatizados

Para executar os 53 testes unitários, de regressão, streaming Excel e Safe Mover (100% passando):
```powershell
.venv\Scripts\pytest -v
```

---

## 📋 Limitações Conhecidas

1. **Heurística de Saves:**
   - O mapeamento de saves em C: é aproximado. Pastas de emuladores compartilhadas por múltiplos jogos (ex.: `C:\Users\Public\Documents\Steam`) e colisões por palavras genéricas no título do jogo requerem revisão visual pelo usuário antes de qualquer ação futura.
2. **Ambientes Headless:**
   - A interface gráfica CustomTkinter requer um servidor gráfico ativo (Windows Desktop). Em ambientes puramente headless (CI sem X11/display), utilize a CLI `python main.py`.
