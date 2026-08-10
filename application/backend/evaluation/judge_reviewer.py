import argparse
import base64
from collections import Counter
from openai import OpenAI
import concurrent.futures
import threading

prompt = """
You are an expert scientific reviewer. Compare the two attached scientific papers in terms of scientific quality, rigor, clarity, and impact.
Provide a detailed comparison and state which paper is superior and why.
Paper 1 is the first paper provided and Paper 2 is the second paper provided.

Provide your output in the following format:
**Paper 1**
[Your detailed comparison of Paper 1]

**Paper 2**
[Your detailed comparison of Paper 2]

**Conclusion**
['Paper_1' or 'Paper_2']

IMPORTANT: you must answer as only 'Paper_1' or 'Paper_2' and nothing else for your conclusion.
"""

vote_tokenizer = {
    "Paper_1": 0,
    "Paper_2": 1,
}

flip_vote_tokenizer = {
    "Paper_1": 1,
    "Paper_2": 0,
}

class JudgeReviewer:
    def __init__(self, pdf1_path, pdf2_path, vote_number=3, model="gpt-5", logfile=None):
        self.pdf1_path = pdf1_path
        self.pdf2_path = pdf2_path
        self.vote_number = vote_number
        self.model = model
        self.client = OpenAI()
        self.price_dict = { # For gpt-5
            "input": 1.25 / 1000000,
            "output": 10.0 / 1000000,
        }
        self.base64_pdf1 = self.encode_pdf_to_base64(self.pdf1_path)
        self.base64_pdf2 = self.encode_pdf_to_base64(self.pdf2_path)
        self.logfile = logfile
        self.log_lock = threading.Lock() if logfile else None

    def encode_pdf_to_base64(self, pdf_path):
        with open(pdf_path, "rb") as f:
            data = f.read()
        return base64.b64encode(data).decode("utf-8")

    def call_openai(self, pdf1_name, pdf1_b64, pdf2_name, pdf2_b64):
        response = self.client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "filename": pdf1_name,
                            "file_data": f"data:application/pdf;base64,{pdf1_b64}",
                        },
                        {
                            "type": "input_file",
                            "filename": pdf2_name,
                            "file_data": f"data:application/pdf;base64,{pdf2_b64}",
                        },
                        {
                            "type": "input_text",
                            "text": prompt,
                        },
                    ],
                },
            ]
        )
        return response

    def extract_conclusion(self, output_text, order = "ab"):
        # Look for the line starting with **Conclusion** and get the next non-empty line
        lines = output_text.splitlines()
        for i, line in enumerate(lines):
            if line.strip().lower().startswith("**conclusion**"):
                # Find the next non-empty line
                for j in range(i+1, len(lines)):
                    candidate = lines[j].strip()
                    if candidate:
                        if order == "ab":
                            return vote_tokenizer[candidate.replace("'", "").replace('"', '').strip()]
                        elif order == "ba":
                            return flip_vote_tokenizer[candidate.replace("'", "").replace('"', '').strip()]
        return None

    def majority_vote(self):
        ab_outputs = []
        ba_outputs = []
        ab_cost = 0.0
        ba_cost = 0.0
        reasonings = []
        print(f"Running {self.vote_number} votes in AB and BA order each...")

        def log_output(text):
            if self.logfile and self.log_lock:
                with self.log_lock:
                    with open(self.logfile, "a") as f:
                        f.write(text)
                        if not text.endswith("\n"):
                            f.write("\n")

        def run_ab():
            response = self.call_openai(self.pdf1_path, self.base64_pdf1, self.pdf2_path, self.base64_pdf2)
            reasoning = response.output_text
            # write to logfile (protected by lock) but do not print reasoning here to avoid concurrent stdout writes
            log_output(reasoning)
            conclusion = self.extract_conclusion(reasoning, order="ab")
            cost = self.price_dict["input"] * response.usage.input_tokens + self.price_dict["output"] * response.usage.output_tokens
            return ("ab", conclusion, cost, reasoning)

        def run_ba():
            response = self.call_openai(self.pdf2_path, self.base64_pdf2, self.pdf1_path, self.base64_pdf1)
            reasoning = response.output_text
            log_output(reasoning)
            conclusion = self.extract_conclusion(reasoning, order="ba")
            cost = self.price_dict["input"] * response.usage.input_tokens + self.price_dict["output"] * response.usage.output_tokens
            return ("ba", conclusion, cost, reasoning)

        futures = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=2*self.vote_number) as executor:
            for i in range(self.vote_number):
                print(f"\n===== AB order run {i+1} =====")
                futures.append(executor.submit(run_ab))
                print(f"\n===== BA order run {i+1} =====")
                futures.append(executor.submit(run_ba))

            for future in concurrent.futures.as_completed(futures):
                order, conclusion, cost, reasoning = future.result()
                # collect reasonings to print later from the main thread
                reasonings.append((order, reasoning))
                if order == "ab":
                    ab_outputs.append(conclusion)
                    ab_cost += cost
                else:
                    ba_outputs.append(conclusion)
                    ba_cost += cost

        all_votes = ab_outputs + ba_outputs
        vote_counts = Counter(all_votes)
        majority = vote_counts.most_common(1)[0][0] if vote_counts else None
        print(f"\nVote counts: {dict(vote_counts)}")
        print(f"Majority vote: {majority}")
        print(f"Total price: ${ab_cost + ba_cost:.4f}")

        # Print collected reasonings now that futures have completed to avoid concurrent stdout writes
        print("\n=== Reasonings from LMs ===")
        for idx, (order, text) in enumerate(reasonings, start=1):
            print(f"\n--- Reasoning {idx} (order={order}) ---")
            print(text)

        return vote_counts, ab_cost + ba_cost, reasonings

def main():
    parser = argparse.ArgumentParser(description="Compare two scientific PDFs using an LLM judge.")
    parser.add_argument("pdf1", type=str, help="Path to the first PDF file.")
    parser.add_argument("pdf2", type=str, help="Path to the second PDF file.")
    parser.add_argument("--vote_number", type=int, default=3, help="Number of votes per order (AB and BA)")
    parser.add_argument("--logfile", type=str, default=None, help="Optional file to write all LLM outputs.")
    args = parser.parse_args()

    if args.logfile is None:
        args.logfile = f"judge_reviewer_{args.pdf1.split('/')[-1]}_v_{args.pdf2.split('/')[-1]}.log"

    judge = JudgeReviewer(args.pdf1, args.pdf2, vote_number=args.vote_number, logfile=args.logfile)
    judge.majority_vote()

if __name__ == "__main__":
    main()
