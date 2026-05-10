import os
from typing import Generator

from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_pinecone import PineconeVectorStore
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document

INDEX_NAME  = "github-explainer"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
GROQ_MODEL  = "llama-3.3-70b-versatile"

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are RepoBot, an expert AI assistant that explains GitHub repositories.

You are given relevant code chunks from a repository as context.
Your job is to answer the user's question clearly and accurately.

Rules:
- Base your answer ONLY on the provided context chunks.
- Always mention the file name(s) your answer comes from.
- Use code blocks when showing code snippets.
- If the user asks how to build, run, or deploy the project, provide the exact terminal commands as code blocks.
- If the user asks for a prompt to recreate, rebuild, or reverse engineer the project, generate a highly detailed, comprehensive prompt that they can copy-paste into another LLM. Include the project structure, tech stack, and core functionality in the prompt.
- If the answer is not in the context, say: "I couldn't find that in the indexed files."
- Be concise but complete. Don't repeat the same information twice.

Context from repository:
{context}
"""

# ---------------------------------------------------------------------------
# Load vectorstore
# ---------------------------------------------------------------------------

def load_vectorstore(namespace: str) -> PineconeVectorStore:
    """Return a Pinecone vectorstore scoped to a specific repo namespace."""
    embedder = HuggingFaceEmbeddings(
        model_name=EMBED_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    return PineconeVectorStore(
        index_name=INDEX_NAME,
        embedding=embedder,
        namespace=namespace,
    )


# ---------------------------------------------------------------------------
# Retrieve relevant chunks
# ---------------------------------------------------------------------------

def retrieve_chunks(question: str, vectorstore: PineconeVectorStore, k: int = 5) -> list[Document]:
    """Return the top-k most semantically similar chunks to the question."""
    docs = vectorstore.similarity_search(question, k=k)
    
    # If the user asks about structure, explicitly fetch PROJECT_STRUCTURE chunks
    q_lower = question.lower()
    existing_content = {d.page_content for d in docs}

    if any(kw in q_lower for kw in ["structure", "files", "folder", "tree", "architecture", "context"]):
        try:
            structure_docs = vectorstore.similarity_search(question, k=20, filter={"source": "PROJECT_STRUCTURE"})
            for sd in structure_docs:
                if sd.page_content not in existing_content:
                    docs.append(sd)
                    existing_content.add(sd.page_content)
        except Exception as e:
            print(f"Error fetching structure docs: {e}")

    # If the user asks about running or building, fetch package.json and README explicitly
    if any(kw in q_lower for kw in ["build", "run", "start", "deploy", "setup", "install", "command"]):
        try:
            build_docs = vectorstore.similarity_search(question, k=10, filter={"source": {"$in": ["package.json", "README.md", "docker-compose.yml", "Makefile"]}})
            for bd in build_docs:
                if bd.page_content not in existing_content:
                    docs.append(bd)
                    existing_content.add(bd.page_content)
        except Exception as e:
            print(f"Error fetching build docs: {e}")
            
    return docs


def format_context(docs: list[Document]) -> str:
    """Format retrieved docs into a single context string for the LLM."""
    parts = []
    for doc in docs:
        source = doc.metadata.get("source", "unknown")
        parts.append(f"### File: `{source}`\n{doc.page_content}")
    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Ask — streaming version (yields string chunks)
# ---------------------------------------------------------------------------

def stream_answer(question: str, vectorstore: PineconeVectorStore, k: int = 5) -> Generator[str, None, None]:
    """
    Retrieve relevant chunks and stream the LLM answer chunk by chunk.
    Usage:  for token in stream_answer(q, vs): print(token, end="")
    """
    docs = retrieve_chunks(question, vectorstore, k=k)
    context = format_context(docs)

    llm = ChatGroq(
        model=GROQ_MODEL,
        temperature=0.2,
        streaming=True,
        api_key=os.environ["GROQ_API_KEY"],
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "{question}"),
    ])

    chain = prompt | llm

    for chunk in chain.stream({"context": context, "question": question}):
        yield chunk.content


# ---------------------------------------------------------------------------
# Ask — non-streaming version (returns full string at once)
# ---------------------------------------------------------------------------

def ask(question: str, vectorstore: PineconeVectorStore, k: int = 5) -> tuple[str, list[Document]]:
    """
    Non-streaming version. Returns (answer_string, source_docs).
    Use this when you need the full answer + sources at the same time.
    """
    docs = retrieve_chunks(question, vectorstore, k=k)
    context = format_context(docs)

    llm = ChatGroq(
        model=GROQ_MODEL,
        temperature=0.2,
        api_key=os.environ["GROQ_API_KEY"],
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "{question}"),
    ])

    chain = prompt | llm
    response = chain.invoke({"context": context, "question": question})
    return response.content, docs