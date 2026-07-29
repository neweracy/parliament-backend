/**
 * Bedrock client creation and invocation.
 *
 * Exports: getClient, invokeClaudeBedrock, isBedrockConfigured, setClient
 */

'use strict';

const {
  BedrockRuntimeClient,
  InvokeModelCommand,
} = require('@aws-sdk/client-bedrock-runtime');

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const MODEL_ID = process.env.BEDROCK_MODEL_ID || 'us.anthropic.claude-haiku-4-5-20251001-v1:0';
const MAX_TOKENS = 4096;

// ---------------------------------------------------------------------------
// Client (supports injection for testing)
// ---------------------------------------------------------------------------

let _client = null;

function getClient() {
  if (_client) return _client;
  _client = new BedrockRuntimeClient({
    region: process.env.AWS_REGION || 'us-east-1',
    credentials: {
      accessKeyId: process.env.AWS_ACCESS_KEY_ID,
      secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY,
    },
  });
  return _client;
}

/**
 * Inject a client instance (for testing / dependency injection).
 * @param {object} client - A BedrockRuntimeClient-compatible object
 */
function setClient(client) {
  _client = client;
}

/**
 * Checks whether AWS credentials are present for Bedrock LLM post-processing.
 *
 * @returns {boolean} true when both AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are set
 */
function isBedrockConfigured() {
  return !!(process.env.AWS_ACCESS_KEY_ID && process.env.AWS_SECRET_ACCESS_KEY);
}

// ---------------------------------------------------------------------------
// Core LLM call
// ---------------------------------------------------------------------------

async function invokeClaudeBedrock(systemPrompt, userMessage) {
  const client = getClient();

  const body = JSON.stringify({
    anthropic_version: 'bedrock-2023-05-31',
    max_tokens: MAX_TOKENS,
    system: systemPrompt,
    messages: [{ role: 'user', content: userMessage }],
  });

  const command = new InvokeModelCommand({
    modelId: MODEL_ID,
    contentType: 'application/json',
    accept: 'application/json',
    body,
  });

  const response = await client.send(command);
  const responseBody = JSON.parse(new TextDecoder().decode(response.body));
  return responseBody.content?.[0]?.text ?? '';
}

module.exports = {
  getClient,
  setClient,
  isBedrockConfigured,
  invokeClaudeBedrock,
  MODEL_ID,
};
