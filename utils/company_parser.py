import os
import Levenshtein
import logging

from config.config import KNOWLEDGE_BASE_DIR

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_company_names_from_md_files(directory):
    company_names = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.md'):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        first_line = f.readline().strip()
                        # 去除 # 和前后空格
                        first_line = first_line.lstrip('#').strip()
                        if first_line:
                            company_names.append(first_line)
                except Exception as e:
                    logging.error(f"Error reading {file_path}: {e}")
    logging.info(f"Successfully retrieved company names: {company_names}")
    return company_names

# 初始化时获取所有company名称
company_names = get_company_names_from_md_files("D:\Test-KB")

# 同义词词典（可根据实际情况扩展）
synonym_dict = {}

def extract_company_name(question):
    # 关键词匹配
    for name in company_names:
        if name in question:
            logging.info(f"Extracted company name by keyword matching: {name}")
            return name

    # 同义词匹配
    for official_name, synonyms in synonym_dict.items():
        for synonym in synonyms:
            if synonym in question:
                logging.info(f"Extracted company name by synonym matching: {official_name}")
                return official_name

    # 模糊匹配
    # min_distance = float('inf')
    # best_match = None
    # for name in company_names:
    #     distance = Levenshtein.distance(name, question)
    #     if distance < min_distance:
    #         min_distance = distance
    #         best_match = name
    # if best_match:
    #     logging.info(f"Extracted hospital name by fuzzy matching: {best_match}")
    return question

def test_extract_company_name():
    test_cases = [
        "你是谁？",
        # 可以添加更多测试用例
    ]
    for question in test_cases:
        result = extract_company_name(question)
        print(f"Question: {question}")
        print(f"Extracted company name: {result}")
        print("-" * 50)

# 封装解析 md 文件第一行获取company名称的方法
def get_company_name_from_md(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            first_line = f.readline().strip()
            # 去除 # 和前后空格
            first_line = first_line.lstrip('#').strip()
            return first_line
    except Exception as e:
        logging.error(f"Error reading {file_path}: {e}")
        return None

if __name__ == "__main__":
    test_extract_company_name()