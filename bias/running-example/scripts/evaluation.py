import re
import csv
import json
import time
import logging
import requests
from pathlib import Path

# Configuration

GUARDME_URL = 'http://trust4ai.us.es:8084/api/v1/metamorphic-tests'

MRS_FILE_PATH = './configuration/metamorphic_relations.json'
SETUP_FILE_PATH = './configuration/setup.json'
INPUT_DIR = './data/execution'
OUTPUT_DIR = './data/evaluation'

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

def save_to_csv(evaluations, file_path):
    if not evaluations:
        return

    header_keys = evaluations[0].keys()
    headers = ['test_id', 'bias_type']

    if 'attribute' in header_keys:
        headers.append('attribute')

    if 'attribute_1' in header_keys and 'attribute_2' in header_keys:
        headers.extend(['attribute_1', 'attribute_2'])

    headers.extend(['scenario', 'prompt_1', 'response_1', 'prompt_2', 'response_2', 'verdict', 'severity'])

    if 'metric_value' in header_keys:
        headers.append('metric_value')

    if 'generation_explanation' in header_keys:
        headers.append('generation_explanation')

    if 'evaluation_explanation' in header_keys:
        headers.append('evaluation_explanation')

    headers.extend(['execution_timestamp', 'evaluation_timestamp'])

    evaluations = [{key: evaluation[key] for key in headers} for evaluation in evaluations]

    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, mode='w', newline='', encoding='utf-8') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(evaluations)

def execute_evaluation(request_body, test_id, mr, judge):
    retries = 0
    while retries < MAX_RETRIES:
        try:
            response = requests.post(f"{GUARDME_URL}/evaluate", json=request_body)
            evaluation_timestamp = time.time()
            response.raise_for_status()
            response_data = response.json()

            return {
                **request_body,
                'verdict': response_data['verdict'],
                'severity': response_data['severity'],
                'evaluation_explanation': re.sub(r'^\[.*?\]:\s*', '', response_data['evaluation_explanation']),
                'evaluation_timestamp': evaluation_timestamp,
                'test_id': test_id,
            }
        except requests.exceptions.HTTPError:
            logger.error(response.text)
            retries += 1
            time.sleep(RETRY_DELAY)
            if retries == MAX_RETRIES:
                logger.info(f'Failed to evaluate test {test_id} for {mr} with {judge} after {MAX_RETRIES} retries')

def execute_comparison(request_body, test_id, mr):
    retries = 0
    while retries < MAX_RETRIES:
        with requests.Session() as session:
            try:
                response = session.post(f"{GUARDME_URL}/compare", json=request_body)
                evaluation_timestamp = time.time()
                response.raise_for_status()
                response_data = response.json()

                return {
                    **request_body,
                    'verdict': response_data['verdict'],
                    'severity': response_data['severity'],
                    'evaluation_timestamp': evaluation_timestamp,
                    'test_id': test_id,
                    'metric_value': response_data['metric_value'],
                }
            except requests.exceptions.HTTPError:
                logger.error(response.text)
                retries += 1
                time.sleep(RETRY_DELAY)
                if retries == MAX_RETRIES:
                    logger.info(f'Failed to evaluate test {test_id} for {mr} after {MAX_RETRIES} retries')

def process_evaluations(metamorphic_relations, execution_config, evaluation_config):
    judge_models = evaluation_config.get('judge_models', [evaluation_config.get('judge_model')])
    judge_temperature = evaluation_config['judge_temperature']
    models = execution_config['models_under_test']

    for judge in judge_models:
        for model in models:
            for mr, mr_info in metamorphic_relations.items():
                input_file = Path(INPUT_DIR) / model / f'{mr}.csv' if len(models) > 1 else Path(INPUT_DIR) / f'{mr}.csv'
                evaluations = []

                if not input_file.exists():
                    continue

                with open(input_file, encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    if not mr_info.get('static_evaluation', False):
                        request_body_template = {
                            'judge_models': [judge],
                            'evaluation_method': mr_info['evaluation_method'],
                            'judge_temperature': judge_temperature,
                        }
                        for row in reader:
                            request_body = request_body_template.copy()
                            request_body['bias_type'] = row['bias_type']
                            request_body.update({key: row[key] for key in ['attribute', 'attribute_1', 'attribute_2'] if key in row})
                            request_body.update({
                                'prompt_1': row['prompt_1'],
                                'response_1': row['response_1'],
                                'prompt_2': row['prompt_2'],
                                'response_2': row['response_2']
                            })

                            evaluation = execute_evaluation(request_body, row['test_id'], mr, judge)
                            if evaluation:
                                evaluation['execution_timestamp'] = row['execution_timestamp']
                                evaluation['scenario'] = row['scenario']
                                evaluations.append(evaluation)
                                message = f'Evaluated test {row["test_id"]} for {mr} on {model} with {judge}'

                                logger.info(message)
                                output_file = Path(OUTPUT_DIR) / 'judge' / model / f'{mr}.csv'

                    else:
                        request_body_template = {
                            'metric': mr_info['evaluation_method'],
                            'evaluation_method': mr_info['evaluation_method'],
                            'judge_temperature': judge_temperature,
                        }

                        if mr_info.get('threshold', False):
                            request_body_template['threshold'] = mr_info['threshold']

                        for row in reader:
                            request_body = request_body_template.copy()
                            request_body.update({
                                'response_1': row['response_1'],
                                'response_2': row['response_2']
                            })

                            evaluation = execute_comparison(request_body, row['test_id'], mr)
                            if evaluation:
                                evaluation['execution_timestamp'] = row['execution_timestamp']
                                evaluation['bias_type'] = row['bias_type']
                                evaluation['prompt_1'] = row['prompt_1']
                                evaluation['prompt_2'] = row['prompt_2']
                                evaluation['scenario'] = row['scenario']
                                evaluations.append(evaluation)
                                logger.info(f'Evaluated test {row["test_id"]} for {mr} on {model}')

                                output_file = Path(OUTPUT_DIR) / 'static' / model / f'{mr}.csv'

                save_to_csv(evaluations, output_file)

def launch_evaluation():
    metamorphic_relations = load_json(MRS_FILE_PATH)
    execution_config = load_json(SETUP_FILE_PATH).get('execution', {})
    evaluation_config = load_json(SETUP_FILE_PATH).get('evaluation', {})

    process_evaluations(metamorphic_relations, execution_config, evaluation_config)

if __name__ == "__main__":
    launch_evaluation()