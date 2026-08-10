ML_PIPELINE_PROMPT = """
You are given a description of a dataset that contains a description of tasks for that dataset.
Your job is to provide a few things:
1. A specific pipeline for how an ML model should be tested. You should specify the construction of a script that runs inference on the ML model.
    - Your pipeline should be robust to many types of machine learning models and frameworks, no matter the type of model used (deep learning, scikit-learn, etc.).
    - You can assume the machine learning model will be contained in a specific script and will be written in Python.
    - You should assume that your pipeline will take in a single input file specifying the testing dataset, and you will compute metrics on that dataset adhering to the specifications of the challenge.
2. Unit tests for the ML model given by the user.
    - These unit tests should make sure that input and output to a given script are correct and that the script handles edge cases.
    - You should be minimal in your design of unit tests - only test the most important cases and the adherence of the script to the requirements of the challenge.
    - Write one script that takes in the inference script and performs all the unit tests on it. This will make unit testing easier for downstream models.
3. A README describing how to run the unit tests and specifying the format of the inference script
    - This README should be explict, well formatted, and easy to understand.
    - It should be written such that an AI agent can follow instructions explicitly to produce a working inference script that can run in your pipeline.

Here is a description of the dataset:

{dataset_description}

You should explicitly output a directory with the unit tests, pipeline, and README in this directory: {output_directory}
    - IMPORTANT: Ensure that your code is well-formatted and you specify in your README exactly what directions the AI agent or user should follow.

Importantly, your final repository should contain the following files:
- run_inference.sh: A script that runs a given testing dataset on the inference script. This should save the predictions of the model on the testing dataset.
- get_metrics.sh: A script that gets the performance metrics on a testing set given the predictions of the model that have been saved from run_inference.sh.
- inference_to_metrics.sh: A combination script that runs both inference and gets the metrics.
- unit_tests.sh: Runs unit tests on the inference script to ensure it works for input and output types that could be given to the model.
You can include other files for the unit tests, but specify such unit tests
"""

ML_EXTRACTION_PROMPT = """Take the directory given to you at {analysis_directory} and extract the machine learning model from it. 
This directory contains code and a final report that was performed by an AI agent who has been exploring this dataset and building a machine learning model to predict the target variables.
Importantly, your job is only to translate the ML model built by the AI agent into a script that can be used in this specified pipeline, not to change the model, even if you think it could be improved.
Then, write a script that adheres to the specifications given to you in the README file in {spec_directory}.
 

Here's the dataset description:

{dataset_description}

Here are the steps you should follow:
1. Read the final report of the agent that explored the dataset at {analysis_directory}/final_report.md
2. Read the README file at: {spec_directory}/README.md
    - This README contains explicit instructions on how to build an inference script for a machine learning model
3. Follow the instructions in the README to build the inference script
    - Adhere strictly to the specifications provided (file format, function signatures, input/output formats, etc.)
    - Ensure your implementation is robust and handles edge cases as described
    - IMPORTANT: Explicitly adapt the ML model built in {analysis_directory}, DO NOT implement a new model.
4. Execute the unit tests that are provided in the directory
    - The README will specify how to run the unit tests
    - Run all unit tests to validate your implementation
    - If any tests fail, debug and fix your inference script until all tests pass

IMPORTANT: Your final output should be a single script that can be run to perform the inference on an external testing dataset.
You should put this script into {analysis_directory}/INFERENCE.py only.
Make sure this script as you write it can pass the unit tests.
"""

ML_AD_EXTRACT = """Take the directory given to you at {analysis_directory} and extract the machine learning model from it.
This directory contains code and a final report that was performed by an AI agent who has been exploring this dataset and building a machine learning model to predict Alzheimer's Disease pathology markers.
Importantly, your job is only to translate the ML model built by the AI agent into a script that can be used in the AD Prediction Challenge pipeline, not to change the model, even if you think it could be improved.

Here's the dataset description:

{dataset_description}

---

## Required Function Signature

Your `INFERENCE.py` must contain a `run_inference` function with this **exact signature** to be compatible with the dual-region evaluation pipeline:

```python
def run_inference(test_a9, test_mtg, model_dir=None):
    \"\"\"
    Run inference on dual-region test data.

    Parameters:
    -----------
    test_a9 : AnnData
        Test dataset from the A9 (substantia nigra) brain region.
        Loaded via scanpy with backed='r' for memory efficiency.
        Contains:
        - test_a9.X: Gene expression matrix (sparse)
        - test_a9.obs: Cell metadata including 'Donor ID' or 'Donor_ID'
        - test_a9.var: Gene information
        - test_a9.obsm: Embeddings (X_umap, X_scVI if available)

    test_mtg : AnnData
        Test dataset from the MTG (middle temporal gyrus) brain region.
        Same structure as test_a9.

    model_dir : str, optional
        Path to directory containing trained model files.
        If None, use models relative to the script location.

    Returns:
    --------
    predictions_df : pd.DataFrame
        DataFrame with one row per unique donor containing predictions.

        REQUIRED COLUMNS:
        - 'Donor ID': str, unique identifier for each donor
        - 'predicted ADNC': str, one of ['Not AD', 'Low', 'Intermediate', 'High']
        - 'predicted Braak': str, one of ['Braak 0', 'Braak I', 'Braak II',
                                          'Braak III', 'Braak IV', 'Braak V', 'Braak VI']
        - 'predicted CERAD': str, one of ['Absent', 'Sparse', 'Moderate', 'Frequent']
        - 'predicted Thal': str, one of ['Thal 0', 'Thal 1', 'Thal 2',
                                         'Thal 3', 'Thal 4', 'Thal 5']
        - 'predicted 6e10': float, range [0, 100]
        - 'predicted AT8': float, range [0, 100]
        - 'predicted GFAP': float, range [0, 100]
        - 'predicted NeuN': float, range [0, 100]
    \"\"\"
```

---

## Critical Requirements

### 1. Function Name and Parameters
- Function MUST be named exactly `run_inference` (case-sensitive)
- MUST accept keyword arguments `test_a9` and `test_mtg` (not positional-only)
- MUST accept optional `model_dir` parameter

### 2. Handling Both Regions
Your inference function receives data from TWO brain regions. You should:
- Extract features from both `test_a9` and `test_mtg`
- Combine or merge features by donor ID
- Handle cases where a donor may appear in one region but not the other

Example pattern:
```python
def run_inference(test_a9, test_mtg, model_dir=None):
    # Extract features from each region
    features_a9 = extract_features(test_a9, region_name='A9')
    features_mtg = extract_features(test_mtg, region_name='MTG')

    # Merge by donor ID
    combined = features_a9.merge(features_mtg, on='Donor_ID', how='outer')

    # Run predictions
    predictions = make_predictions(combined, model_dir)

    return predictions
```

### 3. Donor ID Column
The input data may have donor IDs in either:
- `'Donor ID'` (with space)
- `'Donor_ID'` (with underscore)

Your code should handle both:
```python
donor_col = 'Donor ID' if 'Donor ID' in adata.obs.columns else 'Donor_ID'
```

The OUTPUT DataFrame MUST use `'Donor ID'` (with space).

### 4. Categorical Value Strings
Categorical predictions must match these EXACT strings (case-sensitive):

**ADNC:** `'Not AD'`, `'Low'`, `'Intermediate'`, `'High'`

**Braak:** `'Braak 0'`, `'Braak I'`, `'Braak II'`, `'Braak III'`, `'Braak IV'`, `'Braak V'`, `'Braak VI'`

**CERAD:** `'Absent'`, `'Sparse'`, `'Moderate'`, `'Frequent'`

**Thal:** `'Thal 0'`, `'Thal 1'`, `'Thal 2'`, `'Thal 3'`, `'Thal 4'`, `'Thal 5'`

### 5. Continuous Value Ranges
All continuous predictions must be:
- Numeric (float)
- In range [0, 100]
- Use `.clip(0, 100)` to ensure valid range

### 6. No Duplicate Donors
The output must have exactly one row per donor:
```python
predictions = predictions.drop_duplicates(subset=['Donor ID'])
```

---

## Steps to Follow

1. Read the final report of the agent that explored the dataset at {analysis_directory}/final_report.md
2. Read the README file at: {spec_directory}/README.md
    - This README contains explicit instructions on how to build an inference script
3. Follow the instructions in the README to build the inference script
    - Adhere strictly to the specifications provided above
    - Ensure your implementation handles both brain regions (A9 and MTG)
    - IMPORTANT: Explicitly adapt the ML model built in {analysis_directory}, DO NOT implement a new model
4. Execute the unit tests that are provided in the directory
    - The README will specify how to run the unit tests
    - Run all unit tests to validate your implementation
    - If any tests fail, debug and fix your inference script until all tests pass

---

## Common Mistakes to Avoid

1. Using `test_data` instead of `test_a9, test_mtg` parameters
2. Returning `'Donor_ID'` instead of `'Donor ID'` in output
3. Using lowercase categorical values (`'not ad'` instead of `'Not AD'`)
4. Missing the space in `'Braak 0'` (`'Braak0'` is invalid)
5. Continuous values outside [0, 100] range
6. Duplicate donor IDs in output
7. Missing required columns in output DataFrame
8. Not handling both brain regions properly

---

IMPORTANT: Your final output should be a single script that can be run to perform the inference on an external testing dataset.
You should put this script into {analysis_directory}/INFERENCE.py only.
Make sure this script as you write it can pass the unit tests.
"""
