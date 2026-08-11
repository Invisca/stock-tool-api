# Stock Tool API

Render-ready FastAPI backend adapted from the original zacks_ranks.py.

Build command:
pip install -r requirements.txt

Start command:
uvicorn app:app --host 0.0.0.0 --port $PORT

POST /stocks
Example body:
{
  "tickers": "AMD, AAPL, XOM",
  "delay": 0.25
}
