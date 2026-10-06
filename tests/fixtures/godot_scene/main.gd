extends Node2D
var t := 0.0
var color := Color.RED
func _process(d):
	t += d
	queue_redraw()
func _draw():
	draw_rect(Rect2(Vector2.ZERO, Vector2(640,360)), color)
	draw_circle(Vector2(320 + 200*sin(t*2), 180), 40, Color.WHITE)
func _input(e):
	if e is InputEventKey and e.pressed:
		print("KEY ", e.as_text_keycode())
		color = Color.BLUE if color == Color.RED else Color.RED
	if e is InputEventMouseButton and e.pressed:
		print("CLICK ", e.position)
		color = Color.GREEN
	if e.is_action_pressed("ui_accept"):
		print("ACTION ui_accept")
