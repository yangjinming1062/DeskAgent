import { JsonRpcGatewayClient } from '@/shared/lib/gateway-protocol'

const DEFAULT_GATEWAY_REQUEST_TIMEOUT_MS = 30_000

export class DeskAgentGateway extends JsonRpcGatewayClient {
  constructor() {
    super({
      closedErrorMessage: 'DeskAgent gateway connection closed',
      connectErrorMessage: 'Could not connect to DeskAgent gateway',
      createRequestId: (nextId: number) => nextId,
      notConnectedErrorMessage: 'DeskAgent gateway is not connected',
      requestTimeoutMs: DEFAULT_GATEWAY_REQUEST_TIMEOUT_MS
    })
  }
}
