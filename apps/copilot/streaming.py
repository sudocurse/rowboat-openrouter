from openai import OpenAI
from flask import Flask, request, jsonify, Response, stream_with_context
from pydantic import BaseModel, ValidationError
from typing import List, Dict, Any, Literal
import json
from lib import AgentContext, PromptContext, ToolContext, ChatContext
import os

openai_client = OpenAI(base_url="https://openrouter.ai/api/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4.1")


class UserMessage(BaseModel):
    role: Literal["user"]
    content: str

class AssistantMessage(BaseModel):
    role: Literal["assistant"]
    content: str

with open('copilot_multi_agent.md', 'r', encoding='utf-8') as file:
    copilot_instructions_multi_agent = file.read()

with open('copilot_edit_agent.md', 'r', encoding='utf-8') as file:
    copilot_instructions_edit_agent = file.read()

with open('example_multi_agent_1.md', 'r', encoding='utf-8') as file:
    copilot_multi_agent_example1 = file.read()

with open('current_workflow.md', 'r', encoding='utf-8') as file:
    current_workflow_prompt = file.read()

# Combine the instruction files to create the full multi-agent instructions
streaming_instructions = "\n\n".join([
    copilot_instructions_multi_agent,
    copilot_multi_agent_example1,
    current_workflow_prompt
])

def get_streaming_response(
        messages: List[UserMessage | AssistantMessage],
        workflow_schema: str,
        current_workflow_config: str,
        context: AgentContext | PromptContext | ToolContext | ChatContext | None = None,
) -> Any:
    # if context is provided, create a prompt for the context
    if context:
        match context:
            case AgentContext():
                context_prompt = f"""
**NOTE**: The user is currently working on the following agent:
{context.agentName}
"""
            case PromptContext():
                context_prompt = f"""
**NOTE**: The user is currently working on the following prompt:
{context.promptName}
"""
            case ToolContext():
                context_prompt = f"""
**NOTE**: The user is currently working on the following tool:
{context.toolName}
"""
            case ChatContext():
                context_prompt = f"""
**NOTE**: The user has just tested the following chat using the workflow above and has provided feedback / question below this json dump:
```json
{json.dumps(context.messages)}
```
"""
    else:
        context_prompt = ""

    # add the workflow schema to the system prompt
    sys_prompt = streaming_instructions.replace("{workflow_schema}", workflow_schema)

    # add the current workflow config to the last user message
    last_message = messages[-1]
    last_message.content = f"""
Context:
The current workflow config is:
```
{current_workflow_config}
```

{context_prompt}

User: {last_message.content}
"""

    updated_msgs = [{"role": "system", "content": sys_prompt}] + [
        message.model_dump() for message in messages
    ]

    return openai_client.chat.completions.create(
        model=MODEL_NAME,
        messages=updated_msgs,
        temperature=0.0,
        stream=True
    )

def create_app():
    app = Flask(__name__)

    @app.route('/health', methods=['GET'])
    def health():
        return jsonify({'status': 'ok'})

    @app.route('/chat_stream', methods=['POST'])
    def chat_stream():
        try:
            request_data = request.json
            if not request_data or 'messages' not in request_data:
                return jsonify({'error': 'No messages provided'}), 400

            messages = [
                UserMessage(**msg) if msg['role'] == 'user' else AssistantMessage(**msg)
                for msg in request_data['messages']
            ]

            workflow_schema = request_data.get('workflow_schema', '')
            current_workflow_config = request_data.get('current_workflow_config', '')
            context = None  # You can add context handling if needed

            def generate():
                stream = get_streaming_response(
                    messages=messages,
                    workflow_schema=workflow_schema,
                    current_workflow_config=current_workflow_config,
                    context=context
                )

                for chunk in stream:
                    if chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        yield f"data: {json.dumps({'content': content})}\n\n"

                yield "event: done\ndata: {}\n\n"

            return Response(
                stream_with_context(generate()),
                mimetype='text/event-stream',
                headers={
                    'Cache-Control': 'no-cache',
                    'X-Accel-Buffering': 'no'
                }
            )

        except ValidationError as ve:
            return jsonify({
                'error': 'Invalid request format',
                'details': str(ve)
            }), 400
        except Exception as e:
            return jsonify({
                'error': 'Internal server error',
                'details': str(e)
            }), 500

    return app

if __name__ == '__main__':
    app = create_app()
    print("Starting Flask server...")
    app.run(port=3002, host='0.0.0.0', debug=True) 