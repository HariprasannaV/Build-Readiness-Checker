from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END

# Create the FastAPI application
app = FastAPI(title="Takumi Build Readiness Checker")

# =========================================================
# 1. SEED DATA (Our Warehouse Inventory & Backup Rules)
# =========================================================

# Dictionary storing all available parts, their voltage, and current stock quantity
PARTS = {
    "MTR-48-2K": {"part_no": "MTR-48-2K", "name": "Hub motor 2 kW", "voltage": 48, "stock_qty": 20},
    "CTL-48-80": {"part_no": "CTL-48-80", "name": "Motor controller 80 A", "voltage": 48, "stock_qty": 5},
    "CTL-60-80": {"part_no": "CTL-60-80", "name": "Motor controller 80 A", "voltage": 60, "stock_qty": 50},
    "CTL-48-80-B": {"part_no": "CTL-48-80-B", "name": "Motor controller 80 A (vendor B)", "voltage": 48,
                    "stock_qty": 40},
    "BAT-48-100": {"part_no": "BAT-48-100", "name": "Battery pack 100 Ah", "voltage": 48, "stock_qty": 12},
    "DCDC-48-12": {"part_no": "DCDC-48-12", "name": "DC-DC converter 48 V to 12 V", "voltage": 48, "stock_qty": 25},
}

# Approved alternate parts in priority order:
# Priority 1: CTL-48-80 swaps to CTL-60-80
# Priority 2: CTL-60-80 swaps to CTL-48-80-B
ALTERNATES = {
    "CTL-48-80": "CTL-60-80",
    "CTL-60-80": "CTL-48-80-B"
}

# List to store all completed build records
BUILDS = []


# =========================================================
# 2. INPUT VALIDATION MODELS (Pydantic "Security Guards")
# =========================================================

# Blueprint for a single part line in the Bill of Materials (BOM)
class BOMLine(BaseModel):
    part_no: str
    qty_per_vehicle: int = Field(gt=0)  # gt=0 ensures quantity > 0 (returns 422 error if <= 0)


# Blueprint for the incoming POST /builds request
class BuildRequest(BaseModel):
    vehicle_model: str = "3w_cargo"
    system_voltage: int = Field(default=48, gt=0)
    quantity: int = Field(default=10, gt=0)  # Must build at least 1 vehicle
    bom: list[BOMLine] = [
        BOMLine(part_no="MTR-48-2K", qty_per_vehicle=1),
        BOMLine(part_no="CTL-48-80", qty_per_vehicle=1),
        BOMLine(part_no="BAT-48-100", qty_per_vehicle=1),
        BOMLine(part_no="DCDC-48-12", qty_per_vehicle=1),
    ]


# Blueprint for updating stock via PUT /parts/{part_no}/stock
class StockUpdate(BaseModel):
    stock_qty: int = Field(ge=0)  # ge=0 allows 0 stock, but blocks negative numbers


# =========================================================
# 3. LANGGRAPH WORKFLOW NODES (The 5 Factory Stations)
# =========================================================

# NODE 1: Prepare the initial state before checking parts
def load_parts(state: dict):
    print("\n--- [NODE 1: load_parts] Preparing build state ---")

    # Copy the requested part numbers into 'final_bom' so we can swap parts safely
    state["final_bom"] = [item["part_no"] for item in state["bom"]]
    state["substitutions"] = []  # Empty list to log any part swaps
    state["iterations"] = 0  # Counter for how many times we checked the BOM
    state["blocked"] = False  # Flag to mark if the build cannot proceed

    print(f"Starting BOM parts: {state['final_bom']}")
    return state


# NODE 2: Check Stock (S1) and Voltage (S2) for every part in the BOM
def check_bom(state: dict):
    state["iterations"] += 1
    state["problems"] = []  # Clear old problems at the start of every check

    print(f"\n--- [NODE 2: check_bom] Running Iteration {state['iterations']} ---")

    # Loop through each part in our BOM list
    for i, item in enumerate(state["bom"]):
        p_no = state["final_bom"][i]  # Current part number at position i
        part = PARTS[p_no]  # Look up part details in our PARTS dictionary
        need = item["qty_per_vehicle"] * state["quantity"]  # Total parts needed for all vehicles

        print(f"Checking {p_no}: Need={need}, Stock={part['stock_qty']}, PartVoltage={part['voltage']}V")

        # Rule S1: Check if warehouse stock is less than what we need
        if part["stock_qty"] < need:
            reason = f"S1: need {need}, stock {part['stock_qty']}"
            print(f"  -> FAILED S1 (Stock Check): {reason}")
            state["problems"].append((i, p_no, reason))

        # Rule S2: Check if the part's voltage matches the vehicle's system_voltage
        elif part["voltage"] != state["system_voltage"]:
            reason = f"S2: {part['voltage']} V, build is {state['system_voltage']} V"
            print(f"  -> FAILED S2 (Voltage Check): {reason}")
            state["problems"].append((i, p_no, reason))

        else:
            print(f"  -> PASSED both S1 and S2!")

    return state


# NODE 3: Swap any failing part with its approved alternate part
def substitute(state: dict):
    print("\n--- [NODE 3: substitute] Looking for alternate parts ---")

    # Loop through every problem found in check_bom
    for i, bad_part, reason in state["problems"]:
        # Look up the backup part in our ALTERNATES dictionary
        next_part = ALTERNATES.get(bad_part)

        # If no alternate part exists, we must block the build
        if not next_part:
            print(f"  -> No alternate found for {bad_part}! Build is BLOCKED.")
            state["blocked"] = True
            return state

        # Swap the bad part with the alternate part inside final_bom
        print(f"  -> Swapping {bad_part} ---> {next_part} (Reason: {reason})")
        state["final_bom"][i] = next_part

        # Record the swap in our substitutions history list
        state["substitutions"].append({
            "iteration": state["iterations"],
            "from": bad_part,
            "to": next_part,
            "reason": reason
        })

    print(f"Updated BOM after swap: {state['final_bom']}")
    return state


# NODE 4: Subtract the used parts from our warehouse stock
def reserve(state: dict):
    print("\n--- [NODE 4: reserve] All parts passed! Reserving stock ---")

    for i, item in enumerate(state["bom"]):
        p_no = state["final_bom"][i]
        used_qty = item["qty_per_vehicle"] * state["quantity"]
        old_stock = PARTS[p_no]["stock_qty"]

        # Subtract the needed quantity from stock
        PARTS[p_no]["stock_qty"] -= used_qty
        print(f"  -> {p_no}: Stock reduced from {old_stock} to {PARTS[p_no]['stock_qty']}")

    return state


# NODE 5: Save the final build outcome and status
def save(state: dict):
    print("\n--- [NODE 5: save] Saving final build record ---")

    # Decide the final build status: blocked, substituted, or ready
    if state["blocked"] or state["problems"]:
        status = "blocked"
    elif state["substitutions"]:
        status = "substituted"
    else:
        status = "ready"

    print(f"Final Build Status: {status.upper()} (Total Iterations: {state['iterations']})")

    # Create the final output dictionary matching the required format
    record = {
        "id": len(BUILDS) + 1,
        "vehicle_model": state["vehicle_model"],
        "system_voltage": state["system_voltage"],
        "quantity": state["quantity"],
        "bom": state["bom"],
        "final_bom": state["final_bom"],
        "substitutions": state["substitutions"],
        "status": status,
        "iterations": state["iterations"]
    }

    BUILDS.append(record)
    state["result"] = record
    return state


# =========================================================
# 4. LANGGRAPH FLOWCHART ROUTING (Connecting the Nodes)
# =========================================================

# Traffic cop after 'check_bom': decides where the arrow goes next
def next_after_check(state: dict):
    if not state["problems"]:
        print("[Router] 0 problems found -> Going to 'reserve'")
        return "reserve"
    if state["iterations"] >= 3:
        print("[Router] Max 3 iterations reached -> Going to 'save' (blocked)")
        return "save"
    print("[Router] Problems found & iterations < 3 -> Going to 'substitute'")
    return "substitute"


# Traffic cop after 'substitute': decides whether to loop back or stop
def next_after_substitute(state: dict):
    if state["blocked"]:
        print("[Router] Build is blocked -> Going to 'save'")
        return "save"
    print("[Router] Substitution complete -> Looping back to 'check_bom'")
    return "check_bom"


# Build the StateGraph and register all 5 nodes
graph_builder = StateGraph(dict)
graph_builder.add_node("load_parts", load_parts)
graph_builder.add_node("check_bom", check_bom)
graph_builder.add_node("substitute", substitute)
graph_builder.add_node("reserve", reserve)
graph_builder.add_node("save", save)

# Connect the nodes with edges (arrows)
graph_builder.set_entry_point("load_parts")
graph_builder.add_edge("load_parts", "check_bom")
graph_builder.add_conditional_edges("check_bom", next_after_check)
graph_builder.add_conditional_edges("substitute", next_after_substitute)
graph_builder.add_edge("reserve", "save")
graph_builder.add_edge("save", END)

# Compile the flowchart so it is ready to run
workflow = graph_builder.compile()


# =========================================================
# 5. FASTAPI ENDPOINTS (Web Routes for /docs)
# =========================================================

@app.post("/builds")
def create_build(req: BuildRequest):
    print("\n==========================================")
    print(f"NEW BUILD REQUEST: {req.quantity}x {req.vehicle_model} at {req.system_voltage}V")
    print("==========================================")

    # Check if all requested parts exist in our PARTS dictionary (return 404 if unknown)
    for item in req.bom:
        if item.part_no not in PARTS:
            print(f"[Error] Unknown part requested: {item.part_no}")
            raise HTTPException(status_code=404, detail=f"Unknown part: {item.part_no}")

    # Step 1: Convert Pydantic object to a regular Python dictionary
    input_dict = req.model_dump()
    # Step 2: Run the LangGraph workflow from start to finish
    final_state = workflow.invoke(input_dict)
    # Step 3: Return the saved build record
    return final_state["result"]


@app.get("/builds/{id}")
def get_build(id: int):
    # Search for a build by its ID
    for b in BUILDS:
        if b["id"] == id:
            return b
    raise HTTPException(status_code=404, detail="Build not found")


@app.get("/builds")
def list_builds(status: str = None, limit: int = 10, offset: int = 0):
    # Filter builds by status if provided, then return paginated slice
    filtered = [b for b in BUILDS if not status or b["status"] == status]
    return filtered[offset: offset + limit]


@app.get("/parts")
def get_parts():
    # Return all parts and their current stock
    return list(PARTS.values())


@app.put("/parts/{part_no}/stock")
def update_stock(part_no: str, body: StockUpdate):
    # Update stock quantity for a specific part number
    if part_no not in PARTS:
        raise HTTPException(status_code=404, detail="Part not found")

    PARTS[part_no]["stock_qty"] = body.stock_qty
    print(f"[Stock Update] {part_no} stock manually updated to {body.stock_qty}")
    return PARTS[part_no]