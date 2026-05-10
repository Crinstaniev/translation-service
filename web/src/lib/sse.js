export function parseSseChunk(buffer, incoming) {
  const source = `${buffer}${incoming}`
  const events = []
  const frames = source.split('\n\n')
  const remainder = frames.pop() ?? ''

  for (const frame of frames) {
    const lines = frame.split('\n')
    let event = 'message'
    const dataLines = []

    for (const line of lines) {
      if (line.startsWith('event:')) {
        event = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trim())
      }
    }

    if (dataLines.length === 0) {
      continue
    }

    const raw = dataLines.join('\n')
    let data = raw
    try {
      data = JSON.parse(raw)
    } catch {
      data = raw
    }

    events.push({ event, data })
  }

  return { events, remainder }
}
