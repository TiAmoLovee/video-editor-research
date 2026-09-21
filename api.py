from fastapi import FastAPI

app = FastAPI(
    title="ClipForge MVP-0",
    description="ClipForge 视频处理服务",
    version="0.1.0",
)


@app.get("/health", summary="检查服务是否运行")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "clipforge"}