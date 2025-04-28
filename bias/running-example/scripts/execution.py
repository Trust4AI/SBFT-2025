import csv
import json
import time
import logging
import requests
from pathlib import Path

# Configuration

GENIE_URL = 'http://trust4ai.us.es:8085/api/v1/metamorphic-tests/execute'

MRS_FILE_PATH = './configuration/metamorphic_relations.json'
SETUP_FILE_PATH = './configuration/setup.json'
INPUT_DIR = './data/generation'
OUTPUT_DIR = './data/execution'

MAX_RETRIES = 10
RETRY_DELAY = 20 # seconds

# Set up logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
logger.addHandler(console_handler)

def load_json(file_path):
    with open(file_path, encoding='utf-8') as f:
        return json.load(f)

def save_to_csv(executions, file_path):
    if not executions:
        return

    header_keys = executions[0].keys()
    headers = ['test_id', 'bias_type']

    if 'attribute' in header_keys:
        headers.append('attribute')

    if 'attribute_1' in header_keys and 'attribute_2' in header_keys:
        headers.extend(['attribute_1', 'attribute_2'])

    headers.extend(['scenario', 'prompt_1', 'response_1', 'prompt_2', 'response_2'])

    if 'generation_explanation' in header_keys:
        headers.append('generation_explanation')

    headers.extend(['execution_timestamp'])

    executions = [{key: execution[key] for key in headers} for execution in executions]

    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, mode='w', newline='', encoding='utf-8') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(executions)

def execute_request(request_body, test_id, mr):
    retries = 0
    while retries < MAX_RETRIES:
        try:
            response = requests.post(GENIE_URL, json=request_body)
            execution_timestamp = time.time()
            response.raise_for_status()
            response_data = response.json()

            res = {
                **request_body,
                'response_1': response_data['response_1'],
                'response_2': response_data['response_2'],
                'execution_timestamp': execution_timestamp,
                'test_id': test_id,
            }

            if res['type'] == 'consistency':
                res['prompt_2'] = response_data['prompt_2']

            return res
        except requests.exceptions.HTTPError:
            logger.error(response.text)
            retries += 1
            time.sleep(RETRY_DELAY)
            if retries == MAX_RETRIES:
                logger.info(f'Failed to execute test {test_id} for {mr} after {MAX_RETRIES} retries')

def process_metamorphic_tests(metamorphic_relations, execution_config):
    models = execution_config['models_under_test']

    for model in models:
        for mr, mr_info in metamorphic_relations.items():

            executions = []
            input_file = Path(INPUT_DIR) / f'{mr}.csv'

            with open(input_file, encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    request_body = {
                        'model_name': model,
                        'list_format_response': mr_info['list_format_response'] if 'list_format_response' in mr_info else False,
                        'numeric_format_response': mr_info['numeric_format_response'] if 'numeric_format_response' in mr_info else False,
                        'yes_no_format_response': mr_info['yes_no_format_response'] if 'yes_no_format_response' in mr_info else False,
                        'multiple_choice_format_response': mr_info['multiple_choice_format_response'] if 'multiple_choice_format_response' in mr_info else False,
                        'completion_format_response': mr_info['completion_format_response'] if 'completion_format_response' in mr_info else False,
                        'rank_format_response': mr_info['rank_format_response'] if 'rank_format_response' in mr_info else False,
                        'prompt_1': row['prompt_1'],
                        'prompt_2': row['prompt_2'],
                        'type': 'consistency' if 'consistency' in mr_info['evaluation_method'].lower() else 'comparison',
                    }

                    if 'response_max_length' in mr_info:
                        request_body['response_max_length'] = mr_info['response_max_length']

                    if mr_info.get('exclude_references', False):
                        if 'attribute' in row:
                            request_body['excluded_text'] = [row['attribute']]
                        elif 'attribute_1' in row and 'attribute_2' in row:
                            request_body['excluded_text'] = [row['attribute_1'], row['attribute_2']]

                    execution = execute_request(request_body, row['test_id'], mr)
                    if execution:
                        execution['bias_type'] = row['bias_type']
                        execution['scenario'] = row['scenario']
                        if 'attribute' in row:
                            execution['attribute'] = row['attribute']
                        elif 'attribute_1' in row and 'attribute_2' in row:
                            execution['attribute_1'] = row['attribute_1']
                            execution['attribute_2'] = row['attribute_2']

                        executions.append(execution)
                        logger.info(f'Executed test {row["test_id"]} for {mr} on {model})')
                        output_file = Path(OUTPUT_DIR) / model / f'{mr}.csv'

                        save_to_csv(executions, output_file)

def launch_execution():
    metamorphic_relations = load_json(MRS_FILE_PATH)
    execution_config = load_json(SETUP_FILE_PATH)['execution']

    process_metamorphic_tests(metamorphic_relations, execution_config)


if __name__ == "__main__":
    launch_execution()