setup:
	pip install -r requirements.txt

ingest:
	python3 ingest.py

run:
	streamlit run chat.py

analyze:
	python3 analyze.py
