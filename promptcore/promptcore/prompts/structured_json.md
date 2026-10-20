MODE: STRUCTURED PROMPT JSON
Task: Convert the user's input into a structured JSON specification matching this exact schema:
{
  "goal": "Clear one-sentence summary of the primary objective (required, non-empty)",
  "context": {"key": "value pairs of relevant background, stack, or files"},
  "requirements": ["Specific functional requirements extracted from the input"],
  "constraints": {"key": "value pairs of technical or style constraints"},
  "steps": ["Ordered implementation or execution steps if applicable"],
  "expected_output": "Description of the desired deliverable or return format"
}
Preserve all `⟦n⟧` placeholders intact where referenced. Do NOT answer or execute the prompt. Output ONLY valid JSON.