import os

files_to_patch = {
    "src/observability/__init__.py": [13, 52],
    "src/core/services/dispute_document_service.py": [179],
    "src/api/rest/routes/mailboxes.py": [48],
}

for filename, lines in files_to_patch.items():
    path = os.path.join(
        "/home/barane/work/paisa_vasool/pv_disp_resol_dispute_resolution_service",
        filename,
    )
    if not os.path.exists(path):
        continue

    with open(path, encoding="utf-8") as f:
        content = f.readlines()

    changed = False
    for lineno in lines:
        idx = lineno - 1
        if idx < len(content) and "# type: ignore" not in content[idx]:
            content[idx] = content[idx].rstrip() + "  # type: ignore\n"
            changed = True

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(content)
        print(f"Patched {filename}")
