import asyncio
from ollama import AsyncClient

async def chatbot():
    client = AsyncClient(host='http://192.168.100.242:11434')
    messages = []

    print("Chatbot started! Type 'quit' or 'exit' to end the conversation.\n")

    while True:
        user_input = input("You: ").strip()

        if user_input.lower() in ['quit', 'exit']:
            print("Goodbye!")
            break

        if not user_input:
            continue

        messages.append({'role': 'user', 'content': user_input})

        response = await client.chat(
            model='llama3.1:8b-instruct-q4_K_M',
            messages=messages
        )

        assistant_message = response['message']['content']
        messages.append({'role': 'assistant', 'content': assistant_message})

        print(f"Assistant: {assistant_message}\n")

if __name__ == '__main__':
    asyncio.run(chatbot())
