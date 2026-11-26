"""Smoke test: run with pytest after installing requirements and pytest."""
import io
from PIL import Image
from fastapi.testclient import TestClient
from app import app

def test_project_to_zip():
    c=TestClient(app)
    assert c.get("/api/health").status_code==200
    r=c.post("/api/projects",json={"title":"Smoke Test"})
    assert r.status_code==200
    pid=r.json()["id"]
    assert c.post(f"/api/projects/{pid}/script",json={"text":"场景 1 室内\n男主：你听见了吗？\n\n男人走近窗边。"}).status_code==200
    assert c.post(f"/api/projects/{pid}/parse").json()["shots_created"]>=1
    image=io.BytesIO()
    Image.new("RGB",(64,64)).save(image,format="PNG")
    result=c.post(f"/api/projects/{pid}/assets",data={"kind":"keyframe"},files=[("files",("关键图.png",image.getvalue(),"image/png"))])
    assert result.status_code==200
    result=c.post(f"/api/projects/{pid}/match")
    assert result.json()["mode"]=="heuristic"
    shot=c.get(f"/api/projects/{pid}").json()["shots"][0]
    assert c.patch(f"/api/projects/{pid}/shots/{shot['id']}",json={"status":"approved"}).status_code==200
    zip_response=c.get(f"/api/projects/{pid}/export")
    assert zip_response.status_code==200
    assert zip_response.content[:2]==b"PK"
