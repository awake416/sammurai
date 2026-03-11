# Parallel Processing Architecture

This document describes the architecture and data flow for the parallel processing implementation in the WhatsApp Action Agent.

## Architecture Diagram

The following diagram shows the components involved in the parallel processing pipeline.

```mermaid
graph TD
    CLI[CLI Interface]
    DB[(SQLite DB)]
    GPE[ThreadPoolExecutor: Groups]
    BPE[ThreadPoolExecutor: Batches]
    LLMC[LLMClient]
    LLMP[LiteLLM Proxy]

    CLI --> DB
    CLI --> GPE
    GPE --> DB
    GPE --> BPE
    BPE --> LLMC
    LLMC --> LLMP
```

## Data Flow Diagram

The following diagram illustrates the flow of data from the initial user command to the final TSV output.

```mermaid
flowchart TD
    User([User]) --> CLI[CLI Interface]
    CLI --> DB[(SQLite DB)]
    DB -- Fetch Groups --> CLI
    CLI -- Fan out Groups --> GPE[Group Workers]
    GPE --> DB
    DB -- Fetch Messages --> GPE
    GPE -- Fan out Batches --> BPE[Batch Workers]
    BPE --> LLMC[LLM Client]
    LLMC --> LLMP[LiteLLM Proxy]
    LLMP -- Action Items --> LLMC
    LLMC -- Action Items --> BPE
    BPE -- Collect Results --> GPE
    GPE -- Collect Results --> CLI
    CLI -- Print TSV --> User
```
