This is a brilliant approach. Starting with a **read-only** querying system allows you to perfect the ingestion pipeline, ensure the Markdown formatting is exactly how you want it, and test Hermes's retrieval accuracy before trusting it to automatically delete or modify your task lists.

Here is the complete, comprehensive Product Requirements Document (PRD) to upgrade Sammurai into a fully functional, WhatsApp-native Second Brain.

---

# PRD: Sammurai v2 (The Local Second Brain)

## 1. Executive Summary

**Objective:** Transform Sammurai from a passive WhatsApp group analyzer into an autonomous, self-compiling Second Brain. The system will passively ingest messages, links, and documents from WhatsApp, compile them into a persistent, Git-backed Markdown knowledge base, and allow the user to intuitively query that knowledge base via a conversational AI agent (Hermes) directly within WhatsApp.
**Target Audience:** Power users and professionals seeking a zero-maintenance personal knowledge management (PKM) system that operates entirely within their existing messaging habits.
**Core Philosophy:** Zero-friction capture (WhatsApp), autonomous organization (Karpathy Markdown Wiki), and private local-first execution.

---

## 2. Goals & Success Metrics

### Goals

* **Zero-Maintenance Ingestion:** Users should never have to manually organize a file, tag a note, or categorize a task.
* **Conversational Recall:** Users must be able to ask natural language questions via WhatsApp and receive accurate answers cited directly from their personal data.
* **100% Privacy:** All message databases and Markdown files must remain on the user's local host.

### Success Metrics

* **Ingestion Success Rate:** 99% of daily WhatsApp summaries successfully compiled into the Markdown wiki without overwriting critical historical context.
* **Query Latency:** Sub-10 second response time for WhatsApp queries handled by the Hermes Agent.
* **Zero Context Loss:** The system accurately links new incoming data to existing concept pages (e.g., matching a new "school fee" receipt to the existing `wiki/school.md` page).

---

## 3. System Architecture & Tech Stack

Sammurai v2 acts as a bridge between three distinct systems: the WhatsApp sync engine, the automated knowledge compiler, and the agentic query router.

* **Ingestion & Sync Layer:** `wacli` (WhatsApp CLI) running as a `systemd` background service, syncing messages to a local SQLite database (`wacli.db`).
* **Processing Backend:** Python 3.12+, utilizing `LiteLLM` for model-agnostic routing (e.g., Gemini-2.0-Flash for parsing, Hermes for agentic querying).
* **Storage (The Brain):** A local directory structure version-controlled by Git.
* `CLAUDE.md`: The system schema/prompt dictating wiki rules.
* `raw/`: The destination for Sammurai's daily unstructured digests.
* `wiki/`: The interconnected Markdown files (e.g., `tasks.md`, `finances.md`, `index.md`, `log.md`).


* **Agentic Layer:** Hermes Agent framework equipped with custom Python function-calling tools.

---

## 4. Core Features & Requirements

### Phase 1: The Autonomous Wiki Compiler (Passive Ingestion)

The existing Sammurai product excels at generating a "Daily Digest" from the SQLite database. We will extend this to write to a persistent wiki.

| Feature | Description | Requirement Details |
| --- | --- | --- |
| **Raw Digest Dumping** | Automate the daily digest output to save as a text file. | Save to `raw/digest_YYYY-MM-DD.txt`. Triggered via daily cron job at 11:59 PM. |
| **Wiki Compilation Loop** | A new script that reads `CLAUDE.md` and the new `raw/` file to update the wiki. | Must update `wiki/index.md`, append tasks to `wiki/tasks.md`, update/create concept pages, and log the action in `wiki/log.md`. |
| **Git Version Control** | Automated state freezing to prevent LLM hallucinations from destroying data. | Execute `git add . && git commit -m "Auto-update: [Date]"` after every successful compilation loop. |

### Phase 2: The Agentic WhatsApp Interface (Active Querying - Read Only)

We will introduce a two-way communication loop so the user can chat with their Second Brain.

| Feature | Description | Requirement Details |
| --- | --- | --- |
| **Real-time Intent Router** | A lightweight classifier that monitors incoming messages to the designated "My Brain" chat. | If intent == "Capture" (e.g., forwarded link), do nothing (wait for midnight cron). If intent == "Query" (e.g., a question), trigger the Query Pipeline. |
| **Hermes Read-Only Tools** | Custom Python functions exposed to the Hermes Agent via LiteLLM. | 1. `search_wiki(query: str)`: Greps the `wiki/` directory.<br>

<br>2. `read_file(filepath: str)`: Returns the text of a specific Markdown file. |
| **Agentic Response Engine** | Hermes reads the required files, synthesizes the answer, and prevents hallucinations. | System prompt must strictly enforce: *"Do not invent answers. Only answer based on what you retrieve from the tools. If not found, say so."* |
| **WhatsApp Response Dispatch** | Pushing the final agent response back to the user. | Use `wacli send --chat [JID] --text "[Response]"` to reply directly to the user's WhatsApp message. |

---

## 5. Standard Operating Procedures (User Flows)

### Flow A: Passive Capture & Compilation

1. **User Action:** Throughout the day, the user forwards a PDF, texts a reminder ("Pay school fees by Friday"), and chats in a family group.
2. **System Sync:** `wacli` continuously updates the local SQLite DB.
3. **Midnight Trigger:** Sammurai generates the Daily Digest and drops it into `raw/`.
4. **Compilation:** The LLM reads the digest, creates `wiki/school_fees.md`, updates `wiki/tasks.md`, and links them in `wiki/index.md`. Git commits the changes.

### Flow B: Active Query via WhatsApp

1. **User Action:** User texts their bot on WhatsApp: *"When is my daughter's football class?"*
2. **Intent Router:** Identifies this as a query and pings Hermes.
3. **Agent Logic:** Hermes calls `search_wiki("football class")`.
4. **Retrieval:** Backend returns snippets from `wiki/family_schedule.md`.
5. **Synthesis & Dispatch:** Hermes drafts the response (*"Football class is this Saturday at 10 AM."*) and sends it back to the user's WhatsApp via `wacli`.

---

## 6. Security & Privacy Guardrails

* **Local First:** The `wacli.db` and the Markdown `wiki/` directory must never be uploaded to cloud storage or a vector database provider.
* **LLM API Security:** If using external APIs (e.g., Gemini-2.0-Flash) via LiteLLM, ensure inputs are strictly limited to the necessary context chunks (not uploading the entire database).
* **Read-Only Constraint:** The Hermes Agent is explicitly denied access to any file-writing functions (e.g., no `write_file` or `delete_file` tools) to ensure it cannot accidentally corrupt the Git-backed wiki during queries.

---

## 7. Future Roadmap (Post v2.0)

* **Read/Write Agent Access:** Once the read-only system is trusted, grant Hermes write access so users can text *"Mark the school fee as paid"*, and the agent will autonomously remove it from `wiki/tasks.md`.
* **Outlook/Email Ingestion:** Add an MS Graph API script to dump daily important emails into the `raw/` folder for the exact same compilation pipeline.
* **Web Scraping Agent:** Allow Hermes to autonomously spin up a headless browser to summarize URLs dropped into the WhatsApp chat in real-time, rather than waiting for the midnight digest.
