# -----------------
# python纯后端，集成了数据库操作，无需java端， 支持多轮对话的版本
# #  ==== 流式响应的服务端版本 ======
# #  知识库加载优化：改为增量加载，不用每次重新构建和加载，定义两个参数用来记录已经向量化的数据和文件。
# #  VECTOR_INDEX_FILE = 'vector_index.pkl'
# #  FILE_LIST_FILE = 'file_list.pkl'

# -----------------
import datetime
import gzip
import os
import re
import time
from typing import List

import aiohttp
import faiss
import jieba
import sqlalchemy
import torch
import pickle
import functools
import requests
import json
from fastapi import FastAPI, Query, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader, TextLoader
from langchain_text_splitters import MarkdownTextSplitter
from requests import sessions, Request, Response
from starlette.responses import StreamingResponse
from transformers import AutoModelForSequenceClassification, AutoTokenizer
from langchain.prompts import PromptTemplate
from langchain.schema.runnable import RunnableSequence
from langchain.chains.combine_documents.stuff import create_stuff_documents_chain

from config.config import CHUNK_SIZE, CHUNK_OVERLAP, KNOWLEDGE_BASE_DIR, OLLAMA_MODEL_NAME, EMBEDDING_MODEL_NAME, \
    RERANKER_MODEL_NAME, VECTOR_INDEX_FILE, FILE_LIST_FILE
import logging
from concurrent.futures import ThreadPoolExecutor
import asyncio
from sqlalchemy import create_engine, Column, Integer, String, Text, TIMESTAMP, ForeignKey, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from model.entities import Message, SessionModel, Base, SessionModelDB, MessageDB, KbFile, KbFileModel
# 开启新的会话
import uuid

from utils.company_parser import get_company_name_from_md, extract_company_name

# 配置日志记录
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 数据库配置
SQLALCHEMY_DATABASE_URL = "mysql+pymysql://itrial:!QAZ2wsx@localhost:3306/aaa?charset=utf8mb4"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_size=5,  # 连接池大小
    max_overflow=10,  # 最大溢出连接数
    pool_timeout=30  # 连接超时时间（秒）
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建表
Base.metadata.create_all(bind=engine)

# 初始化 FastAPI 应用
app = FastAPI()

# 配置 CORS 中间件
origins = [
    "http://localhost:8080",  # 根据实际情况修改
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 加载 BAAI/bge-reranker-large 模型和分词器
tokenizer = AutoTokenizer.from_pretrained(RERANKER_MODEL_NAME)
model = AutoModelForSequenceClassification.from_pretrained(RERANKER_MODEL_NAME)

# 异步向量索引构建方法，使用 lru_cache 进行缓存
@functools.lru_cache(maxsize=1)
async def build_vector_index(directory):
    # 加载 BAAI/bge-large-zh-v1.5 嵌入模型
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME)
    documents = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            if file.endswith('.pdf'):
                loader = PyPDFLoader(file_path)
                documents.extend(loader.load())
            elif file.endswith('.docx'):
                loader = Docx2txtLoader(file_path)
                documents.extend(loader.load())
            elif file.endswith('.md'):
                loader = TextLoader(file_path, encoding='utf-8')
                doc = loader.load()[0]
                # 从 md 文件中获取医院名称
                company_name = get_company_name_from_md(file_path)
                doc.metadata = {"company_name": company_name}
                documents.append(doc)

    text_splitter = MarkdownTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    texts = text_splitter.split_documents(documents)
    docsearch = FAISS.from_documents(texts, embeddings)
    # 将索引迁移到 GPU
    if torch.cuda.is_available():
        res = faiss.StandardGpuResources()  # 创建 GPU 资源
        index_gpu = faiss.index_cpu_to_gpu(res, 0, docsearch.index)  # 将 CPU 索引迁移到 GPU
        docsearch.index = index_gpu
    return docsearch

# 异步重排序方法
async def rerank(query, candidates):
    max_length = CHUNK_SIZE  # 模型最大输入长度
    ranked_candidates = []
    for candidate in candidates:
        query_tokens = tokenizer.tokenize(query)
        doc_tokens = tokenizer.tokenize(candidate.page_content)

        logger.info(f"Query tokens length: {len(query_tokens)}")
        logger.info(f"Doc tokens length: {len(doc_tokens)}")

        total_length = len(query_tokens) + len(doc_tokens) + 2  # 假设分词器会添加 2 个特殊词元
        logger.info(f"Original total length: {total_length}")

        if total_length > max_length:
            remaining_length = max_length - len(query_tokens) - 2
            doc_tokens = doc_tokens[:remaining_length]
            logger.info(f"Truncated doc length: {len(doc_tokens)}")

        input_text = tokenizer.convert_tokens_to_string(query_tokens + doc_tokens)
        inputs = tokenizer(input_text, return_tensors='pt', max_length=max_length, truncation=True)

        logger.info(f"Final input length: {len(inputs['input_ids'][0])}")

        with torch.no_grad():
            outputs = model(**inputs)
            logits = outputs.logits
            if logits.shape[1] >= 2:
                score = logits[0][1].item()
            elif logits.shape[1] == 1:
                score = logits[0][0].item()
            else:
                logger.error(f"Unexpected logits shape: {logits.shape}")
                score = 0  # 或者采取其他处理方式
            ranked_candidates.append((candidate, score))

    # 按分数排序
    ranked_candidates.sort(key=lambda x: x[1], reverse=True)
    ranked_candidates = [candidate for candidate, _ in ranked_candidates]

    return ranked_candidates

# 异步处理 llm.stream 结果的函数
async def process_ollama_stream(chunk):
    if 'response' in chunk:
        return chunk['response'].encode('utf-8')
    else:
        logger.error(f"Unexpected chunk format: {chunk}")
        return None

# 异步加载或构建向量索引
async def load_or_build_vector_index(directory):
    if os.path.exists(VECTOR_INDEX_FILE) and os.path.exists(FILE_LIST_FILE):
        # 加载之前保存的向量索引和文件列表
        with gzip.open(VECTOR_INDEX_FILE, 'rb') as f:
            docsearch = pickle.load(f)
        with open(FILE_LIST_FILE, 'rb') as f:
            old_file_list = pickle.load(f)

        # 获取当前知识库目录下的文件列表
        current_file_list = []
        for root, dirs, files in os.walk(directory):
            for file in files:
                current_file_list.append(os.path.join(root, file))

        # 找出新增的文件
        new_files = [file for file in current_file_list if file not in old_file_list]

        if new_files:
            logger.info(f"Found {len(new_files)} new files. Incrementally building vector index...")
            # 对新增的文件进行向量化处理
            embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME)
            new_documents = []

            # 改进：捕获单个文件处理异常
            for file in new_files:
                try:
                    if file.endswith('.pdf'):
                        loader = PyPDFLoader(file)
                        new_documents.extend(loader.load())
                    elif file.endswith('.docx'):
                        loader = Docx2txtLoader(file)
                        new_documents.extend(loader.load())
                    elif file.endswith('.md'):
                        loader = TextLoader(file, encoding='utf-8')
                        doc = loader.load()[0]
                        company_name = get_company_name_from_md(file)
                        doc.metadata = {"company_name": company_name}
                        new_documents.append(doc)
                except Exception as e:
                    logger.error(f"Error processing file {file}: {e}")
                    # 继续处理其他文件

            # 检查是否有有效文档
            if not new_documents:
                logger.warning("No valid documents found in new files. Keeping existing index.")
                return docsearch

            text_splitter = MarkdownTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
            new_texts = text_splitter.split_documents(new_documents)

            # 检查拆分后是否还有文本
            if not new_texts:
                logger.warning("No valid text chunks after splitting. Keeping existing index.")
                return docsearch

            # 打印调试信息
            logger.info(f"Creating embeddings for {len(new_texts)} text chunks")

            # 尝试创建索引
            try:
                # 合并新的向量索引到已有的索引中
                docsearch.add_documents(new_texts)

                # 更新文件列表
                old_file_list.extend(new_files)
                with open(FILE_LIST_FILE, 'wb') as f:
                    pickle.dump(old_file_list, f)

                # 将 GPU 索引转换回 CPU 索引
                if torch.cuda.is_available():
                    docsearch.index = faiss.index_gpu_to_cpu(docsearch.index)

                # 保存更新后的向量索引
                # 使用 gzip 和 pickle 进行压缩并保存
                with gzip.open(VECTOR_INDEX_FILE, 'wb') as f:
                    pickle.dump(docsearch, f)

                # 如果有 GPU，再将索引迁移回 GPU
                if torch.cuda.is_available():
                    res = faiss.StandardGpuResources()
                    docsearch.index = faiss.index_cpu_to_gpu(res, 0, docsearch.index)
            except Exception as e:
                logger.error(f"Error creating FAISS index: {e}")
                # 如果发生错误，返回现有索引
                logger.warning("Using existing index due to error")
                return docsearch
    else:
        logger.info("No existing vector index found. Building vector index from scratch...")
        try:
            docsearch = await build_vector_index(directory)

            # 将 GPU 索引转换回 CPU 索引
            if torch.cuda.is_available():
                docsearch.index = faiss.index_gpu_to_cpu(docsearch.index)

            # 保存向量索引和文件列表
            file_list = []
            for root, dirs, files in os.walk(directory):
                for file in files:
                    file_list.append(os.path.join(root, file))
            with gzip.open(VECTOR_INDEX_FILE, 'wb') as f:
                pickle.dump(docsearch, f)
            with open(FILE_LIST_FILE, 'wb') as f:
                pickle.dump(file_list, f)

            # 如果有 GPU，再将索引迁移回 GPU
            if torch.cuda.is_available():
                res = faiss.StandardGpuResources()
                docsearch.index = faiss.index_cpu_to_gpu(res, 0, docsearch.index)
        except Exception as e:
            logger.error(f"Error building vector index from scratch: {e}")
            # 创建一个空的FAISS索引作为后备
            embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME)
            # 修改创建空FAISS索引的方式
            docsearch = FAISS([], embeddings.embed_query, {}, {})

    return docsearch

# 异步加载本地知识库
# 移除 asyncio.run(load_or_build_vector_index(KNOWLEDGE_BASE_DIR))，避免重复调用
docsearch = None

# 定义新的提示模板（包含历史对话）
prompt_template = """历史对话：
{history}

使用以下文档中的信息来回答问题：
{context}

问题: {question}"""
prompt = PromptTemplate(
    template=prompt_template,
    input_variables=["history", "context", "question"]
)

# 异步生成回答
# 异步生成回答（修改后的函数）
async def generate_answer(question, session_id, db: Session):
    global docsearch
    if docsearch is None:
        docsearch = await load_or_build_vector_index(KNOWLEDGE_BASE_DIR)
    try:
        logger.info(f"Received question: {question}")

        # 获取历史对话（排除当前尚未保存的问题）
        history_messages = db.query(MessageDB).filter(
            MessageDB.session_id == session_id,
            MessageDB.type.in_(['question', 'answer'])
        ).order_by(MessageDB.created_date).all()

        # 构造历史对话字符串
        history_str = ""
        for msg in history_messages:
            if msg.type == 'question':
                history_str += f"用户: {msg.content}\n"
            elif msg.type == 'answer':
                history_str += f"助手: {msg.content}\n"

        company_name = extract_company_name(question)
        logger.info(f"你要查询的医院是： {company_name}")

        # candidates = docsearch.similarity_search(question, k=10)
        candidates = docsearch.similarity_search_with_score(question, k=10)

        if candidates:
            logger.info(f"Found relevant documents in the knowledge base.{len(candidates)}")
            if company_name:
                # candidates = [doc for doc in candidates if doc.metadata.get("company_name") == company_name]
                # 当使用similarity_search_with_score时，它返回的是(document, score)形式的元组列表，而不是单纯的文档列表。您需要修改代码以提取元组中的文档部分。
                candidates = [doc_tuple[0] for doc_tuple in candidates if
                              doc_tuple[0].metadata.get("company_name") == company_name]


            ranked_candidates = await rerank(question, candidates)
            final_results = ranked_candidates[:3]
            logger.info(f"Final results: {final_results}")

            context = "\n".join([doc.page_content for doc in final_results]) if final_results else ""
        else:
            logger.info("No relevant documents found in the knowledge base.")
            context = ""

        # 构造完整提示（包含历史对话和上下文）
        full_prompt = prompt.format(
            history=history_str.strip(),
            context=context,
            question=question
        )

        # 流式请求Ollama（保持原有逻辑）
        url = 'http://localhost:11434/api/generate'
        headers = {'Content-Type': 'application/json'}
        data = {
            "model": OLLAMA_MODEL_NAME,
            "prompt": full_prompt,
            "parameters": {
                "max_tokens": 100,
                "temperature": 0.2
            },
            "stream": True
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as res:
                answer = ""
                async for line in res.content:
                    if line:
                        try:
                            chunk = json.loads(line)
                            result = await process_ollama_stream(chunk)
                            if result:
                                answer_chunk = result.decode('utf-8')
                                answer += answer_chunk
                                # 流式返回数据
                                yield answer_chunk.encode('utf-8')
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON decoding error: {e}, line: {line}")

        # 保存问题和回答到数据库
        question_message = MessageDB(content=question, type='question', session_id=session_id)
        answer_message = MessageDB(content=answer, type='answer', session_id=session_id)
        db.add(question_message)
        db.add(answer_message)
        db.commit()

    except Exception as general_exception:
        error_message = f"General error occurred: {str(general_exception)}"
        logger.error(error_message)
        error_message_obj = MessageDB(
            content=error_message,
            type='error',
            session_id=session_id
        )
        db.add(error_message_obj)
        db.commit()
        yield error_message.encode('utf-8')
        return  # 确保生成器停止


# 获取数据库会话
def get_db():
    max_retries = 3
    retries = 0
    db = None
    while retries < max_retries:
        try:
            db = SessionLocal()
            yield db
            break
        except sqlalchemy.exc.OperationalError as e:
            retries += 1
            logger.error(f"Database connection error (attempt {retries}/{max_retries}): {e}")
            if retries < max_retries:
                time.sleep(2)  # 等待 2 秒后重试
            else:
                raise
        finally:
            if db:
                db.close()


def parse_think_string(input_string):
    try:
        # print("input_string=" +input_string)
        # 使用正则表达式查找 <think> 标签内的内容，re.DOTALL 让 . 匹配包括换行符的任意字符
        think_match = re.search(r'<think>(.*?)</think>', input_string, re.DOTALL)
        # 如果找到匹配项，提取标签内的内容并去除首尾空格，否则赋值为空字符串
        x = think_match.group(1).strip() if think_match else ""
        # print(f"匹配到的 <think> 标签内的内容: {x}")  # 调试输出

        # 移除 <think> 标签及其内容
        pattern = re.compile(r'<think>.*?</think>', re.DOTALL | re.IGNORECASE)
        y = pattern.sub('', input_string).strip()
        # print(f"替换后的内容: {y}")  # 调试输出

        return x, y
    except AttributeError:
        print("未找到 <think> 标签或在解析过程中出现错误。")
        return "", ""


# 定义中间件函数
@app.middleware("http")
async def set_utf8_encoding(request, call_next):
    # 调用下一个中间件或路由处理函数
    response = await call_next(request)
    # 设置响应头
    response.headers['Content-Type'] = 'application/json; charset=utf-8'
    return response


# 对话接口
@app.get("/ask")
async def ask_question(question: str, session_id: int = None, db: Session = Depends(get_db)):
    global docsearch
    if docsearch is None:
        docsearch = await load_or_build_vector_index(KNOWLEDGE_BASE_DIR)
    try:
        if session_id is None:
            new_session = SessionModelDB(session_id=str(uuid.uuid4()))  # 这里可以使用更合适的会话 ID 生成方式
            db.add(new_session)
            db.commit()
            db.refresh(new_session)
            session_id = new_session.id

        headers = {
            'Content-Type': 'text/plain; charset=utf-8',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Transfer-Encoding': 'chunked'
        }
        return StreamingResponse(generate_answer(question, session_id, db), media_type="text/plain", headers=headers)
    except Exception as e:
        logger.error(f"Error in ask_question: {e}")
        return {"error": "An error occurred while processing the request."}
    finally:
        db.close()


@app.post("/new_session")
async def start_new_session(db: Session = Depends(get_db)):
    new_session_id = str(uuid.uuid4())
    new_session = SessionModelDB(session_id=new_session_id)
    db.add(new_session)
    db.commit()
    db.refresh(new_session)
    return {"id": new_session.id}


# 删除会话接口
@app.delete("/delete_session/{session_id}")
async def delete_session(session_id: int, db: Session = Depends(get_db)):
    # 查询要删除的会话
    session_to_delete = db.query(SessionModelDB).filter(SessionModelDB.id == session_id).first()

    # 如果会话不存在，返回 404 错误
    if not session_to_delete:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 删除会话
    db.delete(session_to_delete)
    db.commit()

    return {"message": "会话删除成功"}


# 获取会话历史接口
@app.get("/sessions", response_model=List[SessionModel])
async def get_sessions(db: Session = Depends(get_db)):
    # 查询数据库中的会话历史
    sessions = db.query(SessionModelDB).all()
    # 按创建时间降序排序
    sorted_sessions = sorted(sessions, key=lambda x: x.created_date, reverse=True)
    return sorted_sessions


@app.get("/session/{session_id}/messages", response_model=List[Message])
async def get_session_messages(session_id: int, db: Session = Depends(get_db)):
    messages_db = db.query(MessageDB).filter(MessageDB.session_id == session_id).all()
    messages = []
    for message_db in messages_db:
        content = message_db.content
        # print(f"原始内容: {content}")  # 打印原始内容用于调试

        think, final_content = parse_think_string(content)

        # print(f"解析后的 think: {think}")
        # print(f"解析后的 final_content: {final_content}")
        message = Message(
            id=message_db.id,
            think=think,
            final_content=final_content,
            type=message_db.type,
            created_date=message_db.created_date,
            session_id=message_db.session_id
        )
        messages.append(message)
    return messages


# 新增接口：列出所有知识库文档
@app.get("/list_documents")
async def list_documents():
    documents = []
    for root, dirs, files in os.walk(KNOWLEDGE_BASE_DIR):
        for file in files:
            if file.endswith('.md'):  # 只获取 md 文件
                file_path = os.path.join(root, file)
                company_name  = get_company_name_from_md(file_path)
                if company_name  is not None:
                    documents.append(company_name )
    return {"documents": documents}


# 新增接口：获取文档内容
@app.get("/get_document_content")
async def get_document_content(file_path: str = Query(..., description="要获取内容的文档名称"),
                               db: Session = Depends(get_db)):
    print("预览的文件===" + file_path)
    # 根据文件名从数据库中查询对应的路径
    kb_file = db.query(KbFile).filter(KbFile.name == file_path).first()
    if not kb_file:
        raise HTTPException(status_code=404, detail="文档不存在")

    full_path = kb_file.path
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="文档不存在")
    try:
        with open(full_path, 'r', encoding='utf-8') as file:
            content = file.read()
        return content
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取文档内容出错: {str(e)}")


# 新增接口：删除指定的知识库文档
@app.delete("/delete_document")
async def delete_document(file_path: str = Query(..., description="要删除的文档路径"),
                          db: Session = Depends(get_db)):
    kb_file = db.query(KbFile).filter(KbFile.name == file_path).first()
    if not kb_file:
        raise HTTPException(status_code=404, detail="文档不存在")

    full_path = kb_file.path

    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="文档不存在")
    os.remove(full_path)

    # 重新构建向量索引
    logger.info("Document deleted. Rebuilding vector index...")
    global docsearch
    docsearch = await load_or_build_vector_index(KNOWLEDGE_BASE_DIR)

    return {"message": f"文档 {file_path} 已成功删除"}


# 初始化知识库文件
# 修改 kb_init 函数，确保在更新数据库后重建向量索引
@app.get("/kb_init")
async def kb_init():
    db = SessionLocal()
    inserted_kb_files = []
    try:
        # 清空数据库里的所有 KbFile 记录 - 使用更明确的删除方式
        try:
            count = db.query(KbFile).delete(synchronize_session='fetch')
            db.commit()
            logger.info(f"已删除 {count} 条知识库文件记录")

            # 验证删除是否成功
            remaining = db.query(KbFile).count()
            logger.info(f"剩余知识库文件记录数: {remaining}")

            if remaining > 0:
                logger.warning("数据库记录未完全清空，尝试使用原生SQL")
                db.execute(text("DELETE FROM kb_file"))
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"删除知识库文件记录失败: {e}")
            return {"message": f"知识库文件初始化失败: {e}"}

        # 扫描并添加知识库文件到数据库
        for root, dirs, files in os.walk(KNOWLEDGE_BASE_DIR):
            for file in files:
                file_path = os.path.join(root, file)
                file_ext = os.path.splitext(file)[1].lower()

                if file_ext == '.md':
                    name = get_company_name_from_md(file_path)
                    if not name:
                        name = os.path.splitext(file)[0]  # 如果获取第一行失败，使用文件名
                else:
                    name = os.path.splitext(file)[0]

                new_kb_file = KbFile(name=name, path=file_path)
                db.add(new_kb_file)
                inserted_kb_files.append(new_kb_file)

        db.commit()

        # 重要：强制重建向量索引
        # 删除现有的向量索引和文件列表，以便它们被重新创建
        if os.path.exists(VECTOR_INDEX_FILE):
            os.remove(VECTOR_INDEX_FILE)
        if os.path.exists(FILE_LIST_FILE):
            os.remove(FILE_LIST_FILE)

        # 重新构建向量索引
        logger.info("知识库文件更新，正在重建向量索引...")
        global docsearch
        docsearch = await load_or_build_vector_index(KNOWLEDGE_BASE_DIR)
        logger.info("向量索引重建完成")

        # 将 SQLAlchemy 对象转换为 Pydantic 模型对象
        pydantic_kb_files = [KbFileModel.model_validate(kb_file) for kb_file in inserted_kb_files]

        return {
            "message": "知识库文件初始化成功",
            "inserted_kb_files": pydantic_kb_files
        }
    except Exception as e:
        db.rollback()
        logger.error(f"知识库文件初始化失败: {e}")
        return {"message": f"知识库文件初始化失败: {e}"}
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
