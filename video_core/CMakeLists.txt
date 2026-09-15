cmake_minimum_required(VERSION 3.16)
project(mt11_video_core LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(mt11_video_core src/mt11_video_core.cpp)

if(UNIX)
  target_compile_options(mt11_video_core PRIVATE -Wall -Wextra -Wpedantic)
endif()
