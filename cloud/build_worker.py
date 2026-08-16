import json
import os

src = os.path.join(os.path.dirname(__file__), "formula.json")
out = os.path.join(os.path.dirname(__file__), "formula-worker.js")

with open(src, encoding="utf-8") as fh:
    f = json.load(fh)

js = json.dumps(f, ensure_ascii=True, separators=(",", ":"))

code = (
    "addEventListener('fetch', event => {\n"
    "  event.respondWith(handle(event.request));\n"
    "});\n"
    "const FORMULA = " + js + ";\n"
    "async function handle(request) {\n"
    "  const url = new URL(request.url);\n"
    "  const path = url.pathname;\n"
    '  if (path === "/health") {\n'
    '    return new Response(JSON.stringify({ ok: true }), {\n'
    '      headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }\n'
    "    });\n"
    "  }\n"
    '  if (path === "/formula" || path === "/") {\n'
    "    return new Response(JSON.stringify(FORMULA), {\n"
    '      headers: { "Content-Type": "application/json; charset=utf-8", "Access-Control-Allow-Origin": "*" }\n'
    "    });\n"
    "  }\n"
    '  return new Response(JSON.stringify({ ok: false, error: "not found" }), {\n'
    "    status: 404,\n"
    '    headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }\n'
    "  });\n"
    "}\n"
)

with open(out, "w", encoding="ascii") as fh:
    fh.write(code)

print(len(code))