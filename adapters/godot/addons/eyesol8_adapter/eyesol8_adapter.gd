# Eyesol8 adapter autoload. Does nothing unless EYESOL8_ADAPTER_PORT is set.
#
# Protocol: newline-delimited JSON over TCP on 127.0.0.1:$EYESOL8_ADAPTER_PORT
# (0 picks a free port), one client at a time. Once listening it prints
# "EYESOL8_ADAPTER listening on 127.0.0.1:<port>" with the actual port. The client's first line must be
# {"hello": "<EYESOL8_ADAPTER_TOKEN>"}; the reply is {"ok": true, "engine": ...}.
# After that each request {"id": n, "cmd": "...", "args": {...}} gets one reply
# {"id": n, "ok": true, "result": ...} or {"id": n, "ok": false, "error": "..."}.
# Replies can arrive out of order when commands wait for frames.
extends Node

var _server: TCPServer
var _peer: StreamPeerTCP
var _authed := false
var _token := ""
var _buffer := PackedByteArray()


func _ready() -> void:
	var port := OS.get_environment("EYESOL8_ADAPTER_PORT")
	if port.is_empty():
		set_process(false)
		return
	process_mode = Node.PROCESS_MODE_ALWAYS
	_token = OS.get_environment("EYESOL8_ADAPTER_TOKEN")
	_server = TCPServer.new()
	var err := _server.listen(int(port), "127.0.0.1")
	if err != OK:
		push_error("Eyesol8 adapter could not listen on port %s: %s" % [port, error_string(err)])
		set_process(false)
		return
	print("EYESOL8_ADAPTER listening on 127.0.0.1:%d" % _server.get_local_port())


func _process(_delta: float) -> void:
	# Notice a dropped client before deciding whether a new one may connect.
	if _peer != null:
		_peer.poll()
		if _peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
			_peer = null
	if _server.is_connection_available():
		var incoming := _server.take_connection()
		if _peer == null or _peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
			_peer = incoming
			_authed = false
			_buffer.clear()
		else:
			incoming.disconnect_from_host()
	if _peer == null:
		return
	_peer.poll()
	if _peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
		_peer = null
		return
	var available := _peer.get_available_bytes()
	if available > 0:
		var got := _peer.get_data(available)
		if got[0] == OK:
			_buffer.append_array(got[1])
	var newline := _buffer.find(10)
	while newline != -1:
		var line := _buffer.slice(0, newline).get_string_from_utf8()
		_buffer = _buffer.slice(newline + 1)
		_handle_line(line)
		if _peer == null:
			return
		newline = _buffer.find(10)


func _send(message: Dictionary) -> void:
	if _peer != null and _peer.get_status() == StreamPeerTCP.STATUS_CONNECTED:
		_peer.put_data((JSON.stringify(message) + "\n").to_utf8_buffer())


func _handle_line(line: String) -> void:
	var message = JSON.parse_string(line)
	if typeof(message) != TYPE_DICTIONARY:
		_send({"ok": false, "error": "request is not a JSON object"})
		return
	if not _authed:
		if not _token.is_empty() and message.get("hello") == _token:
			_authed = true
			_send({"ok": true, "engine": Engine.get_version_info().string})
		else:
			_send({"ok": false, "error": "bad hello"})
			_peer.disconnect_from_host()
			_peer = null
		return
	_run(message.get("id"), str(message.get("cmd", "")), message.get("args", {}))


func _run(id, cmd: String, args) -> void:
	if typeof(args) != TYPE_DICTIONARY:
		_send({"id": id, "ok": false, "error": "args must be an object"})
		return
	var reply := {"id": id, "ok": true}
	match cmd:
		"screenshot":
			await RenderingServer.frame_post_draw
			var image := get_viewport().get_texture().get_image()
			reply.result = {
				"png": Marshalls.raw_to_base64(image.save_png_to_buffer()),
				"width": image.get_width(),
				"height": image.get_height(),
			}
		"tree":
			var root := get_tree().root.get_node_or_null(NodePath(str(args.get("root", "/root"))))
			if root == null:
				reply = {"id": id, "ok": false, "error": "no node at %s" % args.get("root")}
			else:
				var nodes := []
				_collect(root, int(args.get("max_depth", 32)), bool(args.get("details", false)), nodes)
				reply.result = nodes
		"action":
			var action := str(args.get("name", ""))
			if not InputMap.has_action(action):
				reply = {"id": id, "ok": false, "error": "unknown action %s" % action}
			else:
				_parse_action(action, true)
				for i in int(args.get("frames", 1)):
					await get_tree().process_frame
				_parse_action(action, false)
				reply.result = true
		"frame_wait":
			for i in int(args.get("n", 1)):
				await get_tree().process_frame
			reply.result = Engine.get_process_frames()
		"time_scale":
			var previous := Engine.time_scale
			if args.has("value"):
				Engine.time_scale = float(args.value)
			reply.result = previous
		_:
			reply = {"id": id, "ok": false, "error": "unknown cmd %s" % cmd}
	_send(reply)


func _parse_action(action: String, pressed: bool) -> void:
	var event := InputEventAction.new()
	event.action = action
	event.pressed = pressed
	event.strength = 1.0 if pressed else 0.0
	Input.parse_input_event(event)


func _collect(node: Node, depth: int, details: bool, out: Array) -> void:
	if node == self:
		return
	var entry := {"path": str(node.get_path()), "name": str(node.name), "type": node.get_class()}
	if details:
		if node is CanvasItem:
			entry.visible = node.is_visible_in_tree()
		if node is Node2D:
			entry.position = [node.global_position.x, node.global_position.y]
		elif node is Control:
			entry.position = [node.global_position.x, node.global_position.y]
		elif node is Node3D:
			var p: Vector3 = node.global_position
			entry.position = [p.x, p.y, p.z]
	out.append(entry)
	if depth <= 0:
		return
	for child in node.get_children():
		_collect(child, depth - 1, details, out)
