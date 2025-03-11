# config.py

# 文本分割参数
CHUNK_SIZE = 400
CHUNK_OVERLAP = 80

SPLIT_SIZE=512

# 知识库目录
# KNOWLEDGE_BASE_DIR = "D:\\KB-files\\"
KNOWLEDGE_BASE_DIR = "./KB-files"

# Ollama 模型名称
OLLAMA_MODEL_NAME = "deepseek-r1:14b"

# 嵌入模型名称
# EMBEDDING_MODEL_NAME = "BAAI/bge-large-zh-v1.5"
# EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_MODEL_NAME = "shibing624/text2vec-base-chinese"
# 重排序模型名称
RERANKER_MODEL_NAME = "BAAI/bge-reranker-large"


#HuggingFace 参数
ACCESS_TOKEN = "your HuggingFace access token"

# 保存向量化数据和文件列表的文件路径
VECTOR_INDEX_FILE = '../data/vector_index.pkl'
FILE_LIST_FILE = '../data/file_list.pkl'

