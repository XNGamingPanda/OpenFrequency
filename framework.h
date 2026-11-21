#pragma once

// Windows 头文件会定义 min/max 宏，和 std::min/std::max 冲突。
#ifndef NOMINMAX
#define NOMINMAX
#endif

#define WIN32_LEAN_AND_MEAN             // 从 Windows 头文件中排除极少使用的内容
// Windows 头文件
#include <windows.h>
