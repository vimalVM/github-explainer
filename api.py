import os
import json
import time
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from fastapi import BackgroundTasks

import firebase_admin
from firebase_admin import credentials, firestore, auth as firebase_auth

load_dotenv()

from ingest import ingest, make_namespace, namespace_has_vectors
from retriever import load_vectorstore, stream_answer

# Initialize Firebase Admin
# In production (Render), we load credentials from an env var.
# Locally, we use the JSON file.
if os.path.exists("firebase-service-account.json"):
    cred = credentials.Certificate("firebase-service-account.json")
elif os.environ.get("FIREBASE_SERVICE_ACCOUNT"):
    import base64
    sa_json = json.loads(base64.b64decode(os.environ["FIREBASE_SERVICE_ACCOUNT"]))
    cred = credentials.Certificate(sa_json)
else:
    raise RuntimeError("No Firebase credentials found. Set FIREBASE_SERVICE_ACCOUNT env var or provide firebase-service-account.json")

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

app = FastAPI(title="RepoBot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allow all origins for dev
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        decoded_token = firebase_auth.verify_id_token(token)
        return decoded_token["uid"]
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid authentication token")

# Global cache for vectorstores
vectorstores = {}
indexing_jobs = {}

class IndexRequest(BaseModel):
    url: str

class ChatRequest(BaseModel):
    namespace: str
    question: str

@app.get("/api/repos")
async def get_repos(uid: str = Depends(get_current_user)):
    repos_ref = db.collection("users").document(uid).collection("repos")
    repos_docs = repos_ref.stream()
    repos = {}
    for doc in repos_docs:
        repo_data = doc.to_dict()
        url = repo_data.get("url")
        if not url:
            url = f"https://github.com/{doc.id.replace('-', '/', 1)}"
        repos[doc.id] = url
    return {"repos": repos}

def process_ingest(url: str, namespace: str, uid: str):
    try:
        indexing_jobs[namespace] = {"status": "indexing", "url": url}
        ns = ingest(url)
        vectorstores[ns] = load_vectorstore(ns)
        
        # Save repo to user's Firestore
        db.collection("users").document(uid).collection("repos").document(ns).set({
            "url": url,
            "indexed_at": firestore.SERVER_TIMESTAMP
        })
        
        indexing_jobs[namespace] = {"status": "completed", "url": url, "namespace": ns}
    except Exception as e:
        indexing_jobs[namespace] = {"status": "error", "detail": str(e), "url": url}

@app.post("/api/index")
async def index_repo(req: IndexRequest, background_tasks: BackgroundTasks, uid: str = Depends(get_current_user)):
    url = req.url
    if not url.startswith("https://github.com/"):
        raise HTTPException(status_code=400, detail="URL must start with https://github.com/")
        
    namespace = make_namespace(url)
    
    # Check if user already has it
    doc_ref = db.collection("users").document(uid).collection("repos").document(namespace)
    doc = doc_ref.get()
    if doc.exists and namespace_has_vectors(namespace):
        if namespace not in vectorstores:
            vectorstores[namespace] = load_vectorstore(namespace)
        return {"namespace": namespace, "status": "already_indexed", "url": url}
        
    background_tasks.add_task(process_ingest, url, namespace, uid)
    indexing_jobs[namespace] = {"status": "indexing", "url": url}
    return {"namespace": namespace, "status": "indexing", "url": url}

@app.get("/api/index/{namespace}/status")
async def get_index_status(namespace: str, uid: str = Depends(get_current_user)):
    if namespace not in indexing_jobs:
        doc_ref = db.collection("users").document(uid).collection("repos").document(namespace)
        doc = doc_ref.get()
        if doc.exists and namespace_has_vectors(namespace):
            return {"status": "completed"}
        return {"status": "unknown"}
    return indexing_jobs[namespace]

@app.get("/api/chat/{namespace}/history")
async def get_chat_history(namespace: str, uid: str = Depends(get_current_user)):
    messages_ref = db.collection("users").document(uid).collection("repos").document(namespace).collection("messages").order_by("timestamp")
    messages = []
    for doc in messages_ref.stream():
        messages.append(doc.to_dict())
    return {"messages": messages}

@app.post("/api/chat")
async def chat(req: ChatRequest, uid: str = Depends(get_current_user)):
    if req.namespace not in vectorstores:
        vectorstores[req.namespace] = load_vectorstore(req.namespace)
        
    vectorstore = vectorstores[req.namespace]
    
    async def event_generator():
        try:
            # Save user message to Firestore
            msg_ref = db.collection("users").document(uid).collection("repos").document(req.namespace).collection("messages").document()
            msg_ref.set({
                "role": "user",
                "content": req.question,
                "timestamp": firestore.SERVER_TIMESTAMP
            })
            
            full_answer = ""
            for chunk in stream_answer(req.question, vectorstore, k=5):
                full_answer += chunk
                yield {"data": json.dumps({"text": chunk})}
                
            # Save AI response to Firestore
            ai_msg_ref = db.collection("users").document(uid).collection("repos").document(req.namespace).collection("messages").document()
            ai_msg_ref.set({
                "role": "assistant",
                "content": full_answer,
                "timestamp": firestore.SERVER_TIMESTAMP
            })
                
            yield {"data": json.dumps({"done": True})}
        except Exception as e:
            yield {"data": json.dumps({"error": str(e)})}
            
    return EventSourceResponse(event_generator())

@app.delete("/api/repos/{namespace}")
async def delete_repo(namespace: str, uid: str = Depends(get_current_user)):
    try:
        repo_ref = db.collection("users").document(uid).collection("repos").document(namespace)
        
        # Delete all messages in the subcollection first
        messages_ref = repo_ref.collection("messages")
        batch_size = 100
        while True:
            docs = list(messages_ref.limit(batch_size).stream())
            if not docs:
                break
            batch = db.batch()
            for doc in docs:
                batch.delete(doc.reference)
            batch.commit()
        
        # Now delete the repo document itself
        repo_ref.delete()
        
        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
