System Prompt: "You are an AI assistant specializing in analyzing WhatsApp group conversations. Your goal is to identify clear action items, their assignees (if specified), and deadlines. Prioritize direct instructions over general discussion. If no explicit deadline is given, suggest 'End of Day (EOD)' for urgent tasks or 'End of Week (EOW)' for less urgent ones. Assume the current date for relative deadlines (e.g., 'tomorrow')."
Tool Definitions: Define tools the AI can use:
parseDate(text): A function to extract dates/times from text.
identifyKeywords(message): A function to find predefined keywords (e.g., "action," "todo," "follow up," "by," "due").
getUserContext(message): A function to identify message sender and potential assignees.
Retrieval Augmented Generation (RAG) Data:
A document explaining common abbreviations used in your groups (e.g., "ASAP," "FYI," "LFG").
A list of common project phases and associated keywords (e.g., "kick-off," "design review," "deployment").
Where to Maintain:
System Prompts: Stored as dedicated configuration files (e.g., .txt, .json, .md) within your project's codebase, potentially managed by version control.
Tool Definitions: Integrated directly into your application's logic, making them callable by the AI.
RAG Data: Stored in a knowledge base, database, or vector store that the AI can query.
Leverage: Ensures consistent behavior, provides the AI with deep domain knowledge for message interpretation, and allows the AI to use specific functions for data extraction. This forms the "brain" of your extension's AI.

