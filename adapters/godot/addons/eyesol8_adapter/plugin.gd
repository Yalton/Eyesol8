@tool
extends EditorPlugin

const AUTOLOAD_NAME := "Eyesol8Adapter"


func _enable_plugin() -> void:
	add_autoload_singleton(AUTOLOAD_NAME, "res://addons/eyesol8_adapter/eyesol8_adapter.gd")


func _disable_plugin() -> void:
	remove_autoload_singleton(AUTOLOAD_NAME)
