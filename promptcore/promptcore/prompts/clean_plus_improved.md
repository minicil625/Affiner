MODE: CLEAN + IMPROVED (JSON)
Task: Return a valid JSON object with two string fields:
- `"clean"`: The minimally cleaned version of the input (fixing grammar, typos, self-corrections, and preserving all `⟦n⟧` placeholders).
- `"enhanced"`: A concise, well-structured prompt (maximum 140 words) that expands the user's intent into clear, actionable instructions for an AI coding or writing assistant, preserving all `⟦n⟧` placeholders and adding brief best-practice structure (e.g. edge cases, types, or verification) without inventing domain facts.
Output ONLY valid JSON matching `{"clean": "...", "enhanced": "..."}`.