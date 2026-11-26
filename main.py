import logging

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pymysql import MySQLError

from ai_chat_plugin import plugin_router
from ai_chat_plugin.storage import PROJECT_DIR, DatabaseConfigError

app = FastAPI(title="AI智能办公助手")
app.include_router(plugin_router)
app.mount("/assets", StaticFiles(directory=PROJECT_DIR / "assets"), name="assets")

logger = logging.getLogger(__name__)


@app.exception_handler(DatabaseConfigError)
@app.exception_handler(MySQLError)
async def database_unavailable(request, error):
    logger.warning("Database request failed: %s", type(error).__name__)
    return JSONResponse(status_code=503, content={"detail": "数据库暂时不可用，请重试"})

# 你的页面
@app.get("/")
def index():
    return FileResponse(PROJECT_DIR / "index.html")

# ======================
# 我漏掉的【启动入口】！
# ======================
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
