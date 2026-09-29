"""固定版本模型的显式下载和本地校验；任务处理时不联网下载。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request

MODEL_ID = "Systran/faster-whisper-base"
REVISION = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
MODEL_HASHES = {
    "config.json": "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a",
    "model.bin": "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9",
    "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
    "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
}
SMALL_SPEC = {
    "repository": "Systran/faster-whisper-small",
    "revision": "536b0662742c02347bc0e980a01041f333bce120",
    "sha256": {
        "config.json": "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
        "model.bin": "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
        "tokenizer.json": "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
        "vocabulary.txt": "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
    },
}


def model_spec(name=None):
    name = name or os.environ.get("CLIPFORGE_ASR_MODEL") or "base"
    if name == "base":
        return {"name": name, "repository": MODEL_ID, "revision": REVISION, "sha256": dict(MODEL_HASHES)}
    if name == "small":
        return {"name": name, **SMALL_SPEC, "sha256": dict(SMALL_SPEC["sha256"])}
    raise ValueError("CLIPFORGE_ASR_MODEL 必须是 base 或 small")


def model_directory(explicit=None, *, model_name=None):
    name = model_spec(model_name)["name"]
    return Path(explicit or os.environ.get("CLIPFORGE_ASR_MODEL_DIR")
                or Path(__file__).resolve().parents[3] / f"models/faster-whisper-{name}").expanduser().resolve()


def verify_model(directory, model_name=None):
    directory = Path(directory)
    spec = model_spec(model_name)
    for name, expected in spec["sha256"].items():
        path = directory / name
        if not path.is_file():
            raise ValueError("转写模型未准备好，请先运行 python -m clipforge.analysis.model 下载或校验模型")
        with path.open("rb") as file:
            actual = hashlib.file_digest(file, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"模型文件校验失败：{name}，请重新准备固定版本模型")
    return {key: spec[key] for key in ("repository", "revision", "sha256")}


def download_model(directory, model_name=None):
    directory = Path(directory)
    spec = model_spec(model_name)
    if directory.exists():
        return verify_model(directory, spec["name"])
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".model-", dir=directory.parent) as temporary:
        folder = Path(temporary) / "model"
        folder.mkdir()
        for name in spec["sha256"]:
            url = f"https://huggingface.co/{spec['repository']}/resolve/{spec['revision']}/{name}"
            print(f"Downloading {name} ...", flush=True)
            with urllib.request.urlopen(url, timeout=60) as response, (folder / name).open("xb") as output:
                shutil.copyfileobj(response, output)
        identity = verify_model(folder, spec["name"])
        # 只发布完整且校验通过的目录，不覆盖已有目录。
        folder.rename(directory)
    return identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--model", choices=("base", "small"), help="默认使用 CLIPFORGE_ASR_MODEL 或 base")
    parser.add_argument("--check", action="store_true", help="只检查本地文件，不联网")
    args = parser.parse_args()
    try:
        directory = model_directory(args.directory, model_name=args.model)
        identity = verify_model(directory, args.model) if args.check else download_model(directory, args.model)
    except (OSError, ValueError) as error:
        parser.exit(1, f"FAIL: {error}\n")
    print(json.dumps(identity, indent=2))
    print(f"PASS: {directory}")


if __name__ == "__main__":
    main()
