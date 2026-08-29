import { ApiClient, ApiError, type RequestDescriptor } from "../api/client";
import type { ApiSchemas } from "../api/contracts";
import { unresolvedHouses, type AuthenticationPort, type AuthenticatedSession, type Credentials, type HouseSelection } from "./session";

const loginRequest: RequestDescriptor = { authentication: "none", house: "none", decoder: "direct", invalidateSessionOn401: false };
const houseRequest: RequestDescriptor = { authentication: "required", house: "none", decoder: "direct", invalidateSessionOn401: true };

function invalidResponse(code: string): ApiError {
  return new ApiError("invalid-response", 200, code, "服务返回了无法识别的响应。");
}

function nonEmpty(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function validLogin(value: ApiSchemas["LoginResponse"]): boolean {
  if (!value || typeof value !== "object") return false;
  if (![value.access_token, value.actor_id, value.display_name, value.community_id, value.community_name, value.token_type].every(nonEmpty)) return false;
  if (value.token_type.toLowerCase() !== "bearer") return false;
  if (!Array.isArray(value.roles) || !value.roles.every(nonEmpty)) return false;
  if (!Array.isArray(value.house_ids) || !value.house_ids.every(nonEmpty)) return false;
  if (new Set(value.house_ids).size !== value.house_ids.length) return false;
  return value.current_house_id == null || nonEmpty(value.current_house_id) && value.house_ids.includes(value.current_house_id);
}

export class AuthenticationService implements AuthenticationPort {
  constructor(private readonly api: ApiClient) {}

  async signIn(credentials: Credentials): Promise<AuthenticatedSession> {
    const body: ApiSchemas["LoginRequest"] = credentials;
    const response = await this.api.request<ApiSchemas["LoginResponse"]>(loginRequest, "/api/auth/login", { method: "POST", body });
    if (!validLogin(response)) throw invalidResponse("AUTH_RESPONSE_INVALID");
    return {
      status: "authenticated", accessToken: response.access_token,
      actor: { id: response.actor_id, displayName: response.display_name, communityId: response.community_id,
        communityName: response.community_name, roles: [...response.roles] },
      houses: unresolvedHouses(response.house_ids), currentHouseId: response.current_house_id ?? null,
    };
  }

  async selectHouse(houseId: string): Promise<HouseSelection> {
    const body: ApiSchemas["HouseSelectionRequest"] = { house_id: houseId };
    const response = await this.api.request<ApiSchemas["HouseSelectionResponse"]>(houseRequest, "/api/auth/house", { method: "POST", body });
    if (response.house_id !== houseId || !nonEmpty(response.building) || !nonEmpty(response.unit) || !nonEmpty(response.room_no)) {
      throw invalidResponse("HOUSE_RESPONSE_INVALID");
    }
    return { houseId: response.house_id, building: response.building, unit: response.unit, roomNo: response.room_no };
  }
}
