import csv
import json
import time
import logging
import requests
from pathlib import Path

# Configuration

MUSE_URL = 'http://trust4ai:8083/api/v1/metamorphic-tests/generate'

MRS_FILE_PATH = './configuration/metamorphic_relations.json'
SETUP_FILE_PATH = './configuration/setup.json'
OUTPUT_DIR = './data/generation'

MAX_RETRIES = 10
RETRY_DELAY = 20 # seconds

# Set up logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
logger.addHandler(console_handler)

def get_metamorphic_relations():
    with open(MRS_FILE_PATH, encoding='utf-8') as f:
        return json.load(f)

def get_generation_config():
    with open(SETUP_FILE_PATH, encoding='utf-8') as f:
        json_object = json.load(f)
        return json_object['generation']
    
def save_to_csv(tests, file_path):
    if not tests:
        return

    for index, test in enumerate(tests, start=1):
        test['test_id'] = index

    header_keys = tests[0].keys()
    headers = ['test_id', 'bias_type']

    if 'attribute' in header_keys:
        headers.append('attribute')

    if 'attribute_1' in header_keys and 'attribute_2' in header_keys:
        headers.extend(['attribute_1', 'attribute_2'])

    headers.extend(['scenario', 'prompt_1', 'prompt_2'])

    if 'generation_explanation' in header_keys:
        headers.append('generation_explanation')

    tests = [{key: test[key] for key in headers} for test in tests]

    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, mode='w', newline='', encoding='utf-8') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(tests)
    
def launch_generation():
    metamorphic_relations = get_metamorphic_relations()
    generation_config = get_generation_config()

    bias_types = generation_config['bias_types'] 
    properties_number = generation_config['properties_number']
    tests_per_property = generation_config['tests_per_property']
    generator_model = generation_config['generator_model']
    explanation = generation_config['explanation']
    generator_temperature = generation_config['generator_temperature']
    generation_feedback = generation_config['generation_feedback']

    for mr in metamorphic_relations:
        mr_info = metamorphic_relations[mr]
        mr_generation_method = mr_info['generation_method']
        mr_invert_prompts = mr_info['invert_prompts'] if 'invert_prompts' in mr_info else False

        tests = []
        scenarios = []

        for bias in bias_types:
            if 'proper_nouns' in mr and bias not in ['gender', 'religion']:
                continue

            request_body = {
                'generator_model': generator_model,
                'generation_method': mr_generation_method,
                'bias_type': bias,
                'properties_number': properties_number,
                'tests_per_property': tests_per_property,
                'explanation': explanation,
                'invert_prompts': mr_invert_prompts,
                'generation_feedback': generation_feedback,
                'scenarios': scenarios,
                'generator_temperature': generator_temperature
            }

            retries = 0
            while retries < MAX_RETRIES:
                try:
                    response = requests.post(MUSE_URL, json=request_body)
                    response.raise_for_status()
                    response_data = response.json()
                    if not explanation:
                        for item in response_data:
                            if 'generation_explanation' in item:
                                item.pop('generation_explanation', None)
                    tests.extend(response.json())

                    for test in response.json():
                        if 'scenario' in test:
                            scenarios.append(test['scenario'])

                    logger.info(f'Generated {bias} bias test cases for {mr}')
                    break
                except requests.exceptions.HTTPError:
                    logger.info(f"Attempt {retries + 1}/{MAX_RETRIES} failed")
                    if type(response.json()) == dict:
                        error_message = response.json().get('error', 'Unknown error')
                    else:
                        error_message = response.text
                    logger.error(error_message)
                    retries += 1
                    time.sleep(RETRY_DELAY)
                    if retries == MAX_RETRIES:
                        logger.info(f'Failed to generate {bias} bias test cases for {mr} after {MAX_RETRIES} retries')
        
        save_to_csv(tests, f'{OUTPUT_DIR}/{mr}.csv')

if __name__ == '__main__':
    launch_generation()