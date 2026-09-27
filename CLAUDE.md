# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**monitoramento-medico-multimodal** is a medical monitoring system for multimodal content (videos, audio, documents, anomalies, alerts). The project is in an early scaffolding phase focused on acquiring and organizing content from Google Drive that will serve as the data source.

## Technology Stack

- **Language**: Python 3.13+ (specified in `.python-version`)
- **Package Manager**: `uv` (fast Python package manager, with standard pip as fallback)
- **Key Dependencies**: 
  - `gdown` (Google Drive folder downloader)
  - `python-dotenv` (environment variable management)

## Development Commands

### Setup & Installation

```bash
# Install dependencies with uv (recommended)
uv sync

# Alternative: standard venv + pip
python -m venv .venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows
pip install "gdown>=6.4.0" "python-dotenv>=1.2.3"
```

### Configuration

```bash
# Copy environment template and configure
cp .env.sample .env
# Edit .env and set GOOGLE_DRIVE_FOLDER_URL to your Drive folder URL
```

Required environment variables:
- `GOOGLE_DRIVE_FOLDER_URL` — Google Drive folder URL (mandatory; script aborts without it)

### Running Scripts

```bash
# Basic sanity check (prints "Hello World")
python main.py

# Download content from Google Drive to conteudos/
python scripts/get_content_script.py

# Verify downloads
find conteudos -type f
```

## Project Structure

```
.
├── conteudos/                    # Downloaded content (gitignored)
├── scripts/
│   └── get_content_script.py     # Google Drive folder downloader
├── teste/
│   └── conteudos/                # Test/destination structure for content
├── main.py                        # Entry point (basic verification)
├── pyproject.toml                # Project metadata & dependencies
├── uv.lock                        # Dependency lock (uv format)
├── .env.sample                    # Environment variables template
├── .python-version                # Python version constraint
└── README.md                      # User-facing documentation
```

## Architecture Notes

### Early-Stage Design

This is a scaffolding project. The current focus is data ingestion and organization:
1. **Content Acquisition**: Download multimodal content (videos, audio, documents) from Google Drive via `gdown`
2. **Organization**: Structure and store content locally in `conteudos/`
3. **Future**: This organized content will become the data source for the monitoring system

### Key Entry Points

- **`main.py`**: Currently a minimal sanity check; will evolve as the system grows
- **`scripts/get_content_script.py`**: Primary workflow—loads `.env`, validates `GOOGLE_DRIVE_FOLDER_URL`, uses `gdown --folder` to recursively download. Note: `gdown` creates a nested folder structure (e.g., `conteudos/conteudos/videos/`)

### Environment & Dependencies

- Uses `uv.lock` for reproducible builds across environments
- Python 3.13+ is required (modern features, performance)
- Minimal dependencies by design (early phase)

## Common Workflows

### End-to-End Demo

```bash
cp .env.sample .env
# Edit .env with your Google Drive folder URL
uv sync
python scripts/get_content_script.py
find conteudos -type f
python main.py
```

### Updating Dependencies

```bash
uv add <package>           # Add a new dependency
uv sync                    # Re-sync environment
```

## Notes for Future Development

- As the project evolves, consider:
  - Adding data validation and transformation modules
  - Building anomaly detection pipelines
  - Alert fusion and aggregation logic
  - API/interface for the monitoring system
- Keep the separation between data acquisition (scripts) and core logic clear
- The `teste/` directory structure suggests test content organization—formalize testing as features are added
