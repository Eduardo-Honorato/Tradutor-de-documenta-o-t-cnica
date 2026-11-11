import hashlib, re, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.translate import translate_file_path
from src.translate_client import redact_secrets

p = Path(sys.argv[1])
h0 = hashlib.sha256(p.read_bytes()).hexdigest()
t0 = time.time()
final, report, ok = translate_file_path(str(p))
print("success:", ok, "| tempo: %.1fs" % (time.time() - t0))
print(redact_secrets(report.format() if hasattr(report, "format") else report))
print("origem inalterada:", hashlib.sha256(p.read_bytes()).hexdigest() == h0)
if final is None:
    sys.exit("sem saida (falha)")
orig = p.read_text(encoding="utf-8")
body = re.sub(r"```.*?```", "", orig, flags=re.S)
codes = re.findall(r"`[^`\n]+`", body)
urls = re.findall(r"\]\(([^)]+)\)", orig) + re.findall(r"https?://[^\s)>]+", orig)
fences = re.findall(r"```.*?```", orig, flags=re.S)
def miss(items):
    return [i for i in items if i not in final]
print("codigo inline ausente:", miss(codes))
print("urls ausentes:", miss(urls))
print("blocos de codigo ausentes:", len(miss(fences)))
print("placeholder vazado:", "\u27e6" in final)
print("----- ORIGINAL -----"); print(redact_secrets(orig))
print("----- TRADUZIDO -----"); print(redact_secrets(final))
