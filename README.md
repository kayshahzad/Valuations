# Aletheia-Intelligence

A multi-agent autonomous framework for financial valuation and investment analysis, utilizing the Manager-Worker pattern.

## Architecture

This system uses **LangGraph** to orchestrate a team of specialized agents:

*   **The Librarian (Data Scraper)**: Ingests real-time financial data (SEC filings, earnings, macro data).
*   **The Fundamentalist (The Math)**: Performs DCF, ROIC, and growth modeling.
*   **The Capital Strategist (The Treasurer)**: Analyzes balance sheets, debt profiles, and capital allocation.
*   **The Contrarian (The Skeptic)**: Identifies market biases and sentiment/crowding.
*   **The Investment Committee (The Lead)**: Synthesizes reports and outputs a conviction score.

## Setup

1.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
2.  Set up environment variables (create a `.env` file):
    ```bash
    OPENAI_API_KEY=your_key_here
    # Optional: FMP_API_KEY=...
    ```
3.  Run a valuation sprint:
    ```bash
    python main.py --ticker AAPL
    ```

## Structure

*   `aletheia/agents/`: Agent definitions.
*   `aletheia/tools/`: Financial and search tools.
*   `aletheia/workflow/`: LangGraph orchestration logic.
