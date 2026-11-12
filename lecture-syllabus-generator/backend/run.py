import uvicorn
from app.runtime import server_options

if __name__ == "__main__":
    uvicorn.run("app.main:app", **server_options())
