Core Purpose: "The primary purpose of this extension is to reduce information overload from WhatsApp groups by surfacing critical action items and their associated deadlines, thereby improving productivity and accountability."
Decision Boundaries/Trade-offs:
"Prioritize accuracy of deadline extraction over speed of processing."
"If an action item is ambiguous, flag it for human review rather than making an assumption."
"Do not generate action items that involve sharing sensitive personal information outside the group."
"Focus on concrete tasks; ignore purely social or emotional content."
Value Hierarchy: "User productivity > deadline accuracy > minimal false positives > comprehensiveness."
Where to Maintain: High-level .md or .json configuration files, potentially in a config/ or intent/ directory within your project. This should be part of your main project documentation.
Leverage: Guides the AI's autonomous decision-making over long periods, ensuring it aligns with the overall vision and ethical considerations of your extension. This prevents the AI from optimizing for the "wrong" metrics (e.g., simply extracting all dates instead of relevant deadlines).
