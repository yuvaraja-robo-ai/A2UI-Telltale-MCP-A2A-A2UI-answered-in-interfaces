"""Vehicle bus access for the Telltale application.

The agent never touches a raw frame. It asks a tool for decoded signals, and
every number that reaches an interface carries the scale the DBC defined for it.
"""
