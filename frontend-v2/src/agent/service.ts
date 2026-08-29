import type { paths } from "../api/generated/schema";
import type { ApiClient, RequestDescriptor } from "../api/client";
import type {
  AgentMemory,
  AgentStreamEvent,
  AgentTurn,
  ConfirmAgentAction,
  ConversationMessage,
  ConversationStatus,
  ConversationSummary,
  CreateAgentMemory,
  DeleteAgentMemory,
  SendAgentMessage,
  UpdateAgentMemory,
} from "./models";
import {
  parseAgentTurn,
  parseConversationList,
  parseConversationMessages,
  parseConversationStatus,
  parseMemories,
  parseMemory,
} from "./parsers";
import { parseAgentSse } from "./sse";

type AgentPath = Extract<keyof paths, `/api/agent/${string}`>;
const conversationsPath: AgentPath = "/api/agent/conversations";
const memoriesPath: AgentPath = "/api/agent/memories";
const descriptor: RequestDescriptor = {
  authentication: "required",
  house: "optional",
  decoder: "envelope",
  invalidateSessionOn401: true,
};

function conversationPath(conversationId: string): AgentPath {
  return `/api/agent/conversations/${encodeURIComponent(conversationId)}` as AgentPath;
}
function messagesPath(conversationId: string): AgentPath {
  return `${conversationPath(conversationId)}/messages` as AgentPath;
}
function confirmationPath(conversationId: string): AgentPath {
  return `${conversationPath(conversationId)}/confirmations` as AgentPath;
}
function memoryPath(memoryId: string): AgentPath {
  return `${memoriesPath}/${encodeURIComponent(memoryId)}` as AgentPath;
}

export class AgentService {
  constructor(private readonly api: ApiClient) {}

  async listConversations(limit = 50, signal?: AbortSignal): Promise<ConversationSummary[]> {
    const value = await this.api.request<unknown>(
      descriptor,
      `${conversationsPath}?limit=${encodeURIComponent(String(limit))}`,
      { method: "GET", signal },
    );
    return parseConversationList(value);
  }

  async getConversation(conversationId: string, signal?: AbortSignal): Promise<ConversationStatus> {
    const value = await this.api.request<unknown>(descriptor, conversationPath(conversationId), {
      method: "GET",
      signal,
    });
    return parseConversationStatus(value);
  }

  async listMessages(conversationId: string, signal?: AbortSignal): Promise<ConversationMessage[]> {
    const value = await this.api.request<unknown>(descriptor, messagesPath(conversationId), {
      method: "GET",
      signal,
    });
    return parseConversationMessages(value);
  }

  async sendMessage(conversationId: string, body: SendAgentMessage, signal?: AbortSignal): Promise<AgentTurn> {
    const value = await this.api.request<unknown>(descriptor, messagesPath(conversationId), {
      method: "POST",
      body,
      signal,
      timeoutMs: 60_000,
    });
    return parseAgentTurn(value);
  }

  async *streamMessage(
    conversationId: string,
    body: SendAgentMessage,
    signal?: AbortSignal,
  ): AsyncGenerator<AgentStreamEvent> {
    const response = await this.api.stream(
      descriptor,
      `${messagesPath(conversationId)}/stream`,
      { method: "POST", body, signal, timeoutMs: 15_000 },
    );
    yield* parseAgentSse(response.body!, signal);
  }

  async confirm(conversationId: string, body: ConfirmAgentAction, signal?: AbortSignal): Promise<AgentTurn> {
    const value = await this.api.request<unknown>(descriptor, confirmationPath(conversationId), {
      method: "POST",
      body,
      signal,
    });
    return parseAgentTurn(value);
  }

  async closeConversation(conversationId: string, signal?: AbortSignal): Promise<void> {
    await this.api.request<unknown>(descriptor, conversationPath(conversationId), {
      method: "DELETE",
      signal,
    });
  }

  async listMemories(signal?: AbortSignal): Promise<AgentMemory[]> {
    return parseMemories(
      await this.api.request<unknown>(descriptor, memoriesPath, { method: "GET", signal }),
    );
  }

  async createMemory(body: CreateAgentMemory, signal?: AbortSignal): Promise<AgentMemory> {
    return parseMemory(
      await this.api.request<unknown>(descriptor, memoriesPath, {
        method: "POST",
        body,
        signal,
      }),
    );
  }

  async updateMemory(memoryId: string, body: UpdateAgentMemory, signal?: AbortSignal): Promise<AgentMemory> {
    return parseMemory(
      await this.api.request<unknown>(descriptor, memoryPath(memoryId), {
        method: "PATCH",
        body,
        signal,
      }),
    );
  }

  async deleteMemory(memoryId: string, body: DeleteAgentMemory, signal?: AbortSignal): Promise<void> {
    await this.api.request<unknown>(descriptor, memoryPath(memoryId), {
      method: "DELETE",
      body,
      signal,
    });
  }
}
