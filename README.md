# SOUQ — Setup & Installation

## 1. Create the MySQL Database

Open MySQL in your terminal:

```bash
mysql -u root -p
```

Enter your MySQL password, then create the database required by the project:

```sql
CREATE DATABASE finalproject2;
EXIT;
```

> Tables are created automatically when the backend starts.

---

## 2. Configure `.env`

Create a `.env` file in the project root (`E:\FINAL-PROJECT2\.env`) and fill in your database and AI settings:

```env
# Database
DB_USER=root
DB_PASSWORD=your_mysql_password
DB_HOST=localhost
DB_PORT=3306
DB_NAME=finalproject2

# AI Assistant (OpenRouter / OpenAI-compatible)
AI_PROVIDER=openai
AI_BASE_URL=https://openrouter.ai/api/v1
AI_API_KEY=your_api_key_here
AI_MODEL=openrouter/free
AI_VISION_MODEL=meta-llama/llama-3.2-11b-vision-instruct:free
```


---

## 3. Run the Backend

Start the FastAPI server from the project folder:

```bash
uvicorn main:app --reload
```

---

## 4. Open the Website

Visit the following link in your browser:

http://127.0.0.1:8000
