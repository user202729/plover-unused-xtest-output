# plover-unused-xtest-output

Output plugin for Plover, send key presses through xtest.

Only use unused key codes in xmodmap to avoid conflict.

Note that this still use the actual modifier keys.
(this can be changed by unsetting and resetting the modifier map;
however it doesn't affect most keyboard capture functionalities)

### Implementation note

The general user expectation would be (on a typical keyboard):

* `Shift(a)` should result in (uppercase) `A`.
* `Shift(bracketleft)` should result in `braceleft`.
* `Shift(end)` should select until the end of the line on most GUI applications
(like a typical shift(end) keyboard press would do)

Therefore, unlike the xtest keyboard emulation plugin, this plugin have to copy
the existing key map on sending key combinations.
