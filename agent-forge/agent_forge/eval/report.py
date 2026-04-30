def render(results):
 lines=["# eval_report"]
 for r in results: lines.append(f"- {r.case_id}: {'PASS' if r.passed else 'FAIL'}")
 return "\n".join(lines)
