import { JsonRpcGatewayClient } from '@/lib/gateway-protocol'

const DEFAULT_GATEWAY_REQUEST_TIMEOUT_MS = 30_000

export class ZastGateway extends JsonRpcGatewayClient {
  constructor() {
    super({
      closedErrorMessage: 'Zast gateway connection closed',
      connectErrorMessage: 'Could not connect to Zast gateway',
      createRequestId: nextId => nextId,
      notConnectedErrorMessage: 'Zast gateway is not connected',
      requestTimeoutMs: DEFAULT_GATEWAY_REQUEST_TIMEOUT_MS
    })
  }
}
