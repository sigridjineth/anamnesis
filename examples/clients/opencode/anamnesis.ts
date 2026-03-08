import type { Plugin } from "@opencode-ai/plugin"

export const AnamnesisPlugin: Plugin = async ({ $, directory }) => {
  const dbPath = `${directory}/.anamnesis/anamnesis.db`

  const emit = async (payload: Record<string, unknown>) => {
    const body = JSON.stringify({
      ...payload,
      cwd: directory,
      projectId: directory,
      ts: Date.now(),
    })
    await $`mkdir -p ${`${directory}/.anamnesis`}`
    await $`printf '%s\n' ${body} | python3 -m agent_memory.hooks.opencode --db ${dbPath} --quiet`
  }

  return {
    "chat.message": async (input, output) => {
      await emit({
        type: "chat.message",
        sessionID: input.sessionID,
        agent: input.agent,
        model: input.model,
        message: output.message,
        parts: output.parts,
      })
    },
    "tool.execute.before": async (input, output) => {
      await emit({
        type: "tool.execute.before",
        sessionID: input.sessionID,
        callID: input.callID,
        tool: input.tool,
        args: output.args,
      })
    },
    "tool.execute.after": async (input, output) => {
      await emit({
        type: "tool.execute.after",
        sessionID: input.sessionID,
        callID: input.callID,
        tool: input.tool,
        title: output.title,
        output: output.output,
        metadata: output.metadata,
      })
    },
    event: async ({ event }) => {
      if (
        event.type === "message.part.updated" ||
        event.type === "file.edited" ||
        event.type === "session.idle"
      ) {
        await emit(event as unknown as Record<string, unknown>)
      }
    },
  }
}

export default AnamnesisPlugin
