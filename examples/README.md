# Examples

Every file here is real output from Merge Monkey, captured with `NO_COLOR=1`.

| Folder | Input | Output |
|---|---|---|
| `demo-backport/` | The demo repo from `git mm demo`: hotfix #812 tested in prod, untested PR #845 on dev, backport of #812 into dev | `01-trace.txt` to `06-after.txt` (one file per step), `report.html` |
| `django/` | Local clone of Django, branches `main` and `stable/6.1.x` | `output.txt`: a real `Backport of ...` commit traced to its original, and a forecast of 15 pending backports |
| `cpython/` | Local clone of CPython, branches `main` and the newest `3.x` | `output.txt`: a miss-islington `(cherry picked from commit ...)` backport traced, and forecast details for conflicting picks |

Regenerate them:

```bash
examples/run_demo_example.sh          # offline
examples/run_real_example.sh <django-clone> <cpython-clone>   # offline, on clones you already have
```

These runs used `--no-ai`, so they show what works without a model. With Ollama serving
`gemma4:e4b`, `git mm explain` adds a Gemma summary and suggestion under each hunk.
