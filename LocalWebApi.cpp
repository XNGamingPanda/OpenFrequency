#include "pch.h"

#include "LocalWebApi.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <charconv>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <limits>
#include <mutex>
#include <string>
#include <string_view>
#include <thread>
#include <type_traits>
#include <unordered_map>
#include <utility>
#include <vector>

#include <winsock2.h>
#include <ws2tcpip.h>

namespace openatc::webapi
{
	namespace
	{
		constexpr std::size_t kMaxHeaderBytes = 16 * 1024;
		constexpr std::size_t kMaxBodyBytes = 64 * 1024;
		constexpr std::size_t kMaxLogLines = 128;
		constexpr int kIoTimeoutMs = 500;
		constexpr long kSelectTimeoutMicroseconds = 150 * 1000;
		constexpr int kMaxJsonDepth = 32;

		// ------------------------------------------------------------------
		// Small string helpers
		// ------------------------------------------------------------------

		std::string_view TrimView(std::string_view text)
		{
			while (!text.empty() &&
			       (text.front() == ' ' || text.front() == '\t' || text.front() == '\r' ||
			        text.front() == '\n'))
			{
				text.remove_prefix(1);
			}
			while (!text.empty() &&
			       (text.back() == ' ' || text.back() == '\t' || text.back() == '\r' ||
			        text.back() == '\n'))
			{
				text.remove_suffix(1);
			}
			return text;
		}

		std::string LowerAscii(std::string_view text)
		{
			std::string result;
			result.reserve(text.size());
			for (const unsigned char c : text)
			{
				result.push_back(static_cast<char>(std::tolower(c)));
			}
			return result;
		}

		std::string UpperAscii(std::string_view text)
		{
			std::string result;
			result.reserve(text.size());
			for (const unsigned char c : text)
			{
				result.push_back(static_cast<char>(std::toupper(c)));
			}
			return result;
		}

		bool StartsWith(std::string_view text, std::string_view prefix)
		{
			return text.size() >= prefix.size() && text.substr(0, prefix.size()) == prefix;
		}

		bool EqualsIgnoreCase(std::string_view left, std::string_view right)
		{
			if (left.size() != right.size())
			{
				return false;
			}
			for (std::size_t i = 0; i < left.size(); ++i)
			{
				if (std::tolower(static_cast<unsigned char>(left[i])) !=
				    std::tolower(static_cast<unsigned char>(right[i])))
				{
					return false;
				}
			}
			return true;
		}

		int HexValue(char c)
		{
			if (c >= '0' && c <= '9')
			{
				return c - '0';
			}
			if (c >= 'a' && c <= 'f')
			{
				return c - 'a' + 10;
			}
			if (c >= 'A' && c <= 'F')
			{
				return c - 'A' + 10;
			}
			return -1;
		}

		std::string UrlDecode(std::string_view text)
		{
			std::string result;
			result.reserve(text.size());
			for (std::size_t i = 0; i < text.size(); ++i)
			{
				const char c = text[i];
				if (c == '+')
				{
					result.push_back(' ');
				}
				else if (c == '%' && i + 2 < text.size())
				{
					const int high = HexValue(text[i + 1]);
					const int low = HexValue(text[i + 2]);
					if (high >= 0 && low >= 0)
					{
						result.push_back(static_cast<char>((high << 4) | low));
						i += 2;
					}
					else
					{
						result.push_back(c);
					}
				}
				else
				{
					result.push_back(c);
				}
			}
			return result;
		}

		std::string QueryParameter(std::string_view query, std::string_view key)
		{
			std::size_t position = 0;
			while (position <= query.size())
			{
				const std::size_t ampersand = query.find('&', position);
				const std::string_view pair = query.substr(
					position, ampersand == std::string_view::npos
					              ? std::string_view::npos
					              : ampersand - position);
				const std::size_t equals = pair.find('=');
				const std::string name = UrlDecode(pair.substr(0, equals));
				if (name == key)
				{
					return equals == std::string_view::npos
						? std::string{}
						: UrlDecode(pair.substr(equals + 1));
				}
				if (ampersand == std::string_view::npos)
				{
					break;
				}
				position = ampersand + 1;
			}
			return {};
		}

		bool ParsePositiveInt(std::string_view text, int& value)
		{
			if (text.empty())
			{
				return false;
			}
			const auto [end, error] = std::from_chars(
				text.data(), text.data() + text.size(), value);
			return error == std::errc{} && end == text.data() + text.size() && value > 0;
		}

		// ------------------------------------------------------------------
		// JSON serialization
		// ------------------------------------------------------------------

		// Returns the length of a valid UTF-8 sequence at |index|, or 0 when the
		// bytes are not valid UTF-8. Keeps the JSON valid even if an X-Plane
		// string dataref contains legacy single-byte text.
		std::size_t Utf8SequenceLength(std::string_view text, std::size_t index)
		{
			const unsigned char lead = static_cast<unsigned char>(text[index]);
			if (lead < 0x80)
			{
				return 1;
			}

			std::size_t length = 0;
			unsigned char secondMin = 0x80;
			unsigned char secondMax = 0xBF;
			if (lead >= 0xC2 && lead <= 0xDF)
			{
				length = 2;
			}
			else if (lead == 0xE0)
			{
				length = 3;
				secondMin = 0xA0;
			}
			else if ((lead >= 0xE1 && lead <= 0xEC) || (lead >= 0xEE && lead <= 0xEF))
			{
				length = 3;
			}
			else if (lead == 0xED)
			{
				length = 3;
				secondMax = 0x9F;
			}
			else if (lead == 0xF0)
			{
				length = 4;
				secondMin = 0x90;
			}
			else if (lead >= 0xF1 && lead <= 0xF3)
			{
				length = 4;
			}
			else if (lead == 0xF4)
			{
				length = 4;
				secondMax = 0x8F;
			}
			else
			{
				return 0;
			}

			if (index + length > text.size())
			{
				return 0;
			}
			const unsigned char second = static_cast<unsigned char>(text[index + 1]);
			if (second < secondMin || second > secondMax)
			{
				return 0;
			}
			for (std::size_t offset = 2; offset < length; ++offset)
			{
				const unsigned char continuation =
					static_cast<unsigned char>(text[index + offset]);
				if (continuation < 0x80 || continuation > 0xBF)
				{
					return 0;
				}
			}
			return length;
		}

		void AppendJsonString(std::string& out, std::string_view text)
		{
			out.push_back('"');
			for (std::size_t index = 0; index < text.size();)
			{
				const unsigned char c = static_cast<unsigned char>(text[index]);
				if (c >= 0x80)
				{
					const std::size_t length = Utf8SequenceLength(text, index);
					if (length == 0)
					{
						out += "\\ufffd";
						++index;
					}
					else
					{
						out.append(text.data() + index, length);
						index += length;
					}
					continue;
				}

				switch (c)
				{
				case '"': out += "\\\""; break;
				case '\\': out += "\\\\"; break;
				case '\b': out += "\\b"; break;
				case '\f': out += "\\f"; break;
				case '\n': out += "\\n"; break;
				case '\r': out += "\\r"; break;
				case '\t': out += "\\t"; break;
				default:
					if (c < 0x20)
					{
						char buffer[7] = {};
						std::snprintf(
							buffer, sizeof(buffer), "\\u%04x", static_cast<unsigned int>(c));
						out += buffer;
					}
					else
					{
						out.push_back(static_cast<char>(c));
					}
					break;
				}
				++index;
			}
			out.push_back('"');
		}

		void AppendJsonNumber(std::string& out, double value)
		{
			// NaN/Inf would produce invalid JSON; X-Plane occasionally returns
			// transient non-finite values while the aircraft is loading.
			if (!std::isfinite(value))
			{
				out.push_back('0');
				return;
			}
			char buffer[40] = {};
			const int written = std::snprintf(buffer, sizeof(buffer), "%.15g", value);
			if (written > 0)
			{
				out.append(buffer, static_cast<std::size_t>(written));
			}
			else
			{
				out.push_back('0');
			}
		}

		void AppendJsonValue(std::string& out, const DatarefValue& value)
		{
			std::visit(
				[&out](const auto& item)
				{
					using T = std::decay_t<decltype(item)>;
					if constexpr (std::is_same_v<T, double>)
					{
						AppendJsonNumber(out, item);
					}
					else if constexpr (std::is_same_v<T, std::int64_t>)
					{
						out += std::to_string(item);
					}
					else if constexpr (std::is_same_v<T, std::string>)
					{
						AppendJsonString(out, item);
					}
					else
					{
						out.push_back('[');
						bool first = true;
						for (const auto& element : item)
						{
							if (!first)
							{
								out.push_back(',');
							}
							first = false;
							if constexpr (std::is_same_v<T, std::vector<double>>)
							{
								AppendJsonNumber(out, element);
							}
							else if constexpr (std::is_same_v<T, std::vector<std::int64_t>>)
							{
								out += std::to_string(element);
							}
							else
							{
								AppendJsonString(out, element);
							}
						}
						out.push_back(']');
					}
				},
				value);
		}

		const char* DatarefTypeName(DatarefType type)
		{
			switch (type)
			{
			case DatarefType::Float: return "float";
			case DatarefType::Double: return "double";
			case DatarefType::Int: return "int";
			case DatarefType::FloatArray: return "float_array";
			case DatarefType::IntArray: return "int_array";
			case DatarefType::ByteString: return "string";
			case DatarefType::FlightIdArray: return "string_array";
			}
			return "float";
		}

		std::string DatarefMetadataJson(const DatarefInfo& info)
		{
			std::string out;
			out.reserve(96);
			out += "{\"id\":";
			out += std::to_string(info.id);
			out += ",\"name\":";
			AppendJsonString(out, info.name);
			out += ",\"type\":\"";
			out += DatarefTypeName(info.type);
			out += "\"}";
			return out;
		}

		std::string CommandMetadataJson(const CommandInfo& info)
		{
			std::string out;
			out.reserve(96);
			out += "{\"id\":";
			out += std::to_string(info.id);
			out += ",\"name\":";
			AppendJsonString(out, info.name);
			out += '}';
			return out;
		}

		// ------------------------------------------------------------------
		// Minimal JSON parser (PATCH bodies only)
		// ------------------------------------------------------------------

		struct JsonNode
		{
			enum class Kind
			{
				Null,
				Bool,
				Number,
				String,
				Array,
				Object,
			};

			Kind kind = Kind::Null;
			bool boolean = false;
			double number = 0.0;
			std::string string;
			std::vector<JsonNode> array;
			std::vector<std::pair<std::string, JsonNode>> object;
		};

		class JsonParser
		{
		public:
			explicit JsonParser(std::string_view text) : text_(text) {}

			std::optional<JsonNode> Parse()
			{
				SkipWhitespace();
				std::optional<JsonNode> root = ParseValue(0);
				if (!root)
				{
					return std::nullopt;
				}
				SkipWhitespace();
				if (position_ != text_.size())
				{
					return std::nullopt;
				}
				return root;
			}

		private:
			void SkipWhitespace()
			{
				while (position_ < text_.size())
				{
					const char c = text_[position_];
					if (c != ' ' && c != '\t' && c != '\r' && c != '\n')
					{
						break;
					}
					++position_;
				}
			}

			bool Consume(char expected)
			{
				if (position_ >= text_.size() || text_[position_] != expected)
				{
					return false;
				}
				++position_;
				return true;
			}

			std::optional<JsonNode> ParseValue(int depth)
			{
				if (depth > kMaxJsonDepth)
				{
					return std::nullopt;
				}
				SkipWhitespace();
				if (position_ >= text_.size())
				{
					return std::nullopt;
				}
				switch (text_[position_])
				{
				case '{': return ParseObject(depth);
				case '[': return ParseArray(depth);
				case '"':
				{
					std::optional<std::string> decoded = ParseString();
					if (!decoded)
					{
						return std::nullopt;
					}
					JsonNode node;
					node.kind = JsonNode::Kind::String;
					node.string = std::move(*decoded);
					return node;
				}
				case 't': return ParseLiteral("true", true);
				case 'f': return ParseLiteral("false", false);
				case 'n': return ParseNull();
				default: return ParseNumber();
				}
			}

			std::optional<JsonNode> ParseObject(int depth)
			{
				if (!Consume('{'))
				{
					return std::nullopt;
				}
				JsonNode node;
				node.kind = JsonNode::Kind::Object;
				SkipWhitespace();
				if (Consume('}'))
				{
					return node;
				}
				for (;;)
				{
					SkipWhitespace();
					std::optional<std::string> key = ParseString();
					if (!key)
					{
						return std::nullopt;
					}
					SkipWhitespace();
					if (!Consume(':'))
					{
						return std::nullopt;
					}
					std::optional<JsonNode> value = ParseValue(depth + 1);
					if (!value)
					{
						return std::nullopt;
					}
					node.object.emplace_back(std::move(*key), std::move(*value));
					SkipWhitespace();
					if (Consume('}'))
					{
						return node;
					}
					if (!Consume(','))
					{
						return std::nullopt;
					}
				}
			}

			std::optional<JsonNode> ParseArray(int depth)
			{
				if (!Consume('['))
				{
					return std::nullopt;
				}
				JsonNode node;
				node.kind = JsonNode::Kind::Array;
				SkipWhitespace();
				if (Consume(']'))
				{
					return node;
				}
				for (;;)
				{
					std::optional<JsonNode> value = ParseValue(depth + 1);
					if (!value)
					{
						return std::nullopt;
					}
					node.array.push_back(std::move(*value));
					SkipWhitespace();
					if (Consume(']'))
					{
						return node;
					}
					if (!Consume(','))
					{
						return std::nullopt;
					}
				}
			}

			std::optional<JsonNode> ParseLiteral(std::string_view literal, bool boolean)
			{
				if (text_.substr(position_, literal.size()) != literal)
				{
					return std::nullopt;
				}
				position_ += literal.size();
				JsonNode node;
				node.kind = JsonNode::Kind::Bool;
				node.boolean = boolean;
				return node;
			}

			std::optional<JsonNode> ParseNull()
			{
				if (text_.substr(position_, 4) != "null")
				{
					return std::nullopt;
				}
				position_ += 4;
				return JsonNode{};
			}

			std::optional<JsonNode> ParseNumber()
			{
				if (position_ >= text_.size())
				{
					return std::nullopt;
				}
				const char first = text_[position_];
				if (first != '-' && (first < '0' || first > '9'))
				{
					return std::nullopt;
				}
				const char* begin = text_.data() + position_;
				const char* end = text_.data() + text_.size();
				double number = 0.0;
				const auto [next, error] = std::from_chars(begin, end, number);
				if (error != std::errc{} || next == begin)
				{
					return std::nullopt;
				}
				position_ = static_cast<std::size_t>(next - text_.data());
				JsonNode node;
				node.kind = JsonNode::Kind::Number;
				node.number = number;
				return node;
			}

			bool ParseHex4(std::uint32_t& value)
			{
				if (position_ + 4 > text_.size())
				{
					return false;
				}
				std::uint32_t result = 0;
				for (int i = 0; i < 4; ++i)
				{
					const int digit = HexValue(text_[position_ + i]);
					if (digit < 0)
					{
						return false;
					}
					result = (result << 4) | static_cast<std::uint32_t>(digit);
				}
				position_ += 4;
				value = result;
				return true;
			}

			static void AppendUtf8(std::string& out, std::uint32_t code)
			{
				if (code <= 0x7F)
				{
					out.push_back(static_cast<char>(code));
				}
				else if (code <= 0x7FF)
				{
					out.push_back(static_cast<char>(0xC0 | (code >> 6)));
					out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
				}
				else if (code <= 0xFFFF)
				{
					out.push_back(static_cast<char>(0xE0 | (code >> 12)));
					out.push_back(static_cast<char>(0x80 | ((code >> 6) & 0x3F)));
					out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
				}
				else
				{
					out.push_back(static_cast<char>(0xF0 | (code >> 18)));
					out.push_back(static_cast<char>(0x80 | ((code >> 12) & 0x3F)));
					out.push_back(static_cast<char>(0x80 | ((code >> 6) & 0x3F)));
					out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
				}
			}

			std::optional<std::string> ParseString()
			{
				if (!Consume('"'))
				{
					return std::nullopt;
				}
				std::string out;
				while (position_ < text_.size())
				{
					const char c = text_[position_++];
					if (c == '"')
					{
						return out;
					}
					if (c != '\\')
					{
						out.push_back(c);
						continue;
					}
					if (position_ >= text_.size())
					{
						return std::nullopt;
					}
					const char escape = text_[position_++];
					switch (escape)
					{
					case '"': out.push_back('"'); break;
					case '\\': out.push_back('\\'); break;
					case '/': out.push_back('/'); break;
					case 'b': out.push_back('\b'); break;
					case 'f': out.push_back('\f'); break;
					case 'n': out.push_back('\n'); break;
					case 'r': out.push_back('\r'); break;
					case 't': out.push_back('\t'); break;
					case 'u':
					{
						std::uint32_t code = 0;
						if (!ParseHex4(code))
						{
							return std::nullopt;
						}
						if (code >= 0xD800 && code <= 0xDBFF)
						{
							// Combine a UTF-16 surrogate pair when present.
							const std::size_t saved = position_;
							if (position_ + 1 < text_.size() && text_[position_] == '\\' &&
							    text_[position_ + 1] == 'u')
							{
								position_ += 2;
								std::uint32_t low = 0;
								if (ParseHex4(low) && low >= 0xDC00 && low <= 0xDFFF)
								{
									code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00);
								}
								else
								{
									position_ = saved;
								}
							}
						}
						if (code >= 0xD800 && code <= 0xDFFF)
						{
							// Lone surrogate: emit the Unicode replacement char so
							// the output stays valid UTF-8.
							code = 0xFFFD;
						}
						AppendUtf8(out, code);
						break;
					}
					default: return std::nullopt;
					}
				}
				return std::nullopt;
			}

			std::string_view text_;
			std::size_t position_ = 0;
		};

		std::optional<DatarefValue> ToDatarefValue(const JsonNode& node)
		{
			switch (node.kind)
			{
			case JsonNode::Kind::Number:
				return DatarefValue{node.number};
			case JsonNode::Kind::Bool:
				return DatarefValue{static_cast<std::int64_t>(node.boolean ? 1 : 0)};
			case JsonNode::Kind::String:
				return DatarefValue{node.string};
			case JsonNode::Kind::Array:
			{
				bool allStrings = true;
				bool allNumbers = true;
				for (const JsonNode& element : node.array)
				{
					if (element.kind != JsonNode::Kind::String)
					{
						allStrings = false;
					}
					if (element.kind != JsonNode::Kind::Number &&
					    element.kind != JsonNode::Kind::Bool)
					{
						allNumbers = false;
					}
				}
				// Numbers first so an empty array maps to a numeric array.
				if (allNumbers)
				{
					std::vector<double> values;
					values.reserve(node.array.size());
					for (const JsonNode& element : node.array)
					{
						values.push_back(element.kind == JsonNode::Kind::Number
							? element.number
							: (element.boolean ? 1.0 : 0.0));
					}
					return DatarefValue{std::move(values)};
				}
				if (allStrings)
				{
					std::vector<std::string> values;
					values.reserve(node.array.size());
					for (const JsonNode& element : node.array)
					{
						values.push_back(element.string);
					}
					return DatarefValue{std::move(values)};
				}
				return std::nullopt;
			}
			case JsonNode::Kind::Object:
				for (const auto& [key, value] : node.object)
				{
					if (key == "value")
					{
						return ToDatarefValue(value);
					}
				}
				return std::nullopt;
			case JsonNode::Kind::Null:
			default:
				return std::nullopt;
			}
		}

		// ------------------------------------------------------------------
		// HTTP request/response plumbing
		// ------------------------------------------------------------------

		struct HttpRequest
		{
			std::string method;
			std::string target;
			std::string version;
			std::vector<std::pair<std::string, std::string>> headers;
			std::string body;

			std::string_view Header(std::string_view name) const
			{
				const std::string lowered = LowerAscii(name);
				for (const auto& [key, value] : headers)
				{
					if (key == lowered)
					{
						return value;
					}
				}
				return {};
			}

			std::string Path() const
			{
				const std::size_t query = target.find('?');
				return target.substr(0, query == std::string::npos ? target.size() : query);
			}

			std::string Query() const
			{
				const std::size_t query = target.find('?');
				return query == std::string::npos ? std::string{} : target.substr(query + 1);
			}

			bool KeepAlive() const
			{
				const std::string_view connection = Header("connection");
				if (EqualsIgnoreCase(connection, "close"))
				{
					return false;
				}
				if (EqualsIgnoreCase(connection, "keep-alive"))
				{
					return true;
				}
				return version == "HTTP/1.1";
			}
		};

		struct HttpResponse
		{
			int status = 200;
			std::string body;
		};

		bool ParseRequestHead(std::string_view head, HttpRequest& request)
		{
			const std::size_t firstLineEnd = head.find("\r\n");
			const std::string_view requestLine =
				firstLineEnd == std::string_view::npos ? head : head.substr(0, firstLineEnd);
			const std::size_t firstSpace = requestLine.find(' ');
			const std::size_t secondSpace = firstSpace == std::string_view::npos
				? std::string_view::npos
				: requestLine.find(' ', firstSpace + 1);
			if (firstSpace == std::string_view::npos || secondSpace == std::string_view::npos)
			{
				return false;
			}
			request.method = UpperAscii(requestLine.substr(0, firstSpace));
			request.target = std::string(
				requestLine.substr(firstSpace + 1, secondSpace - firstSpace - 1));
			request.version = std::string(requestLine.substr(secondSpace + 1));
			if (request.method.empty() || request.target.empty())
			{
				return false;
			}
			if (request.version != "HTTP/1.1" && request.version != "HTTP/1.0")
			{
				return false;
			}

			std::size_t position = firstLineEnd == std::string_view::npos
				? head.size()
				: firstLineEnd + 2;
			while (position < head.size())
			{
				const std::size_t lineEnd = head.find("\r\n", position);
				const std::string_view line = head.substr(
					position,
					lineEnd == std::string_view::npos ? std::string_view::npos : lineEnd - position);
				const std::size_t colon = line.find(':');
				if (colon != std::string_view::npos)
				{
					const std::string name = LowerAscii(TrimView(line.substr(0, colon)));
					const std::string value(TrimView(line.substr(colon + 1)));
					if (!name.empty())
					{
						request.headers.emplace_back(name, value);
					}
				}
				if (lineEnd == std::string_view::npos)
				{
					break;
				}
				position = lineEnd + 2;
			}
			return true;
		}

		const char* ReasonPhrase(int status)
		{
			switch (status)
			{
			case 200: return "OK";
			case 204: return "No Content";
			case 400: return "Bad Request";
			case 404: return "Not Found";
			case 405: return "Method Not Allowed";
			case 408: return "Request Timeout";
			case 413: return "Payload Too Large";
			case 431: return "Request Header Fields Too Large";
			case 500: return "Internal Server Error";
			case 503: return "Service Unavailable";
			default: return "Unknown";
			}
		}

		class UniqueSocket
		{
		public:
			UniqueSocket() = default;
			explicit UniqueSocket(SOCKET handle) : handle_(handle) {}
			~UniqueSocket() { Reset(); }

			UniqueSocket(const UniqueSocket&) = delete;
			UniqueSocket& operator=(const UniqueSocket&) = delete;

			UniqueSocket(UniqueSocket&& other) noexcept
				: handle_(std::exchange(other.handle_, INVALID_SOCKET))
			{
			}

			UniqueSocket& operator=(UniqueSocket&& other) noexcept
			{
				if (this != &other)
				{
					Reset();
					handle_ = std::exchange(other.handle_, INVALID_SOCKET);
				}
				return *this;
			}

			SOCKET Get() const noexcept { return handle_; }
			bool Valid() const noexcept { return handle_ != INVALID_SOCKET; }

			void Reset() noexcept
			{
				if (handle_ != INVALID_SOCKET)
				{
					closesocket(handle_);
					handle_ = INVALID_SOCKET;
				}
			}

		private:
			SOCKET handle_ = INVALID_SOCKET;
		};

		bool SendAll(SOCKET socket, std::string_view data)
		{
			std::size_t offset = 0;
			while (offset < data.size())
			{
				const int chunk = static_cast<int>(
					std::min<std::size_t>(data.size() - offset, 1u << 20));
				const int sent = send(socket, data.data() + offset, chunk, 0);
				if (sent <= 0)
				{
					return false;
				}
				offset += static_cast<std::size_t>(sent);
			}
			return true;
		}

		bool SendResponse(SOCKET socket, const HttpResponse& response, bool keepAlive)
		{
			std::string head;
			head.reserve(256);
			head += "HTTP/1.1 ";
			head += std::to_string(response.status);
			head += ' ';
			head += ReasonPhrase(response.status);
			head += "\r\n";
			head += "Content-Type: application/json; charset=utf-8\r\n";
			head += "Content-Length: ";
			head += std::to_string(response.body.size());
			head += "\r\n";
			head += "Connection: ";
			head += keepAlive ? "keep-alive" : "close";
			head += "\r\n";
			head += "Access-Control-Allow-Origin: *\r\n";
			head += "Access-Control-Allow-Methods: GET, PATCH, POST, OPTIONS\r\n";
			head += "Access-Control-Allow-Headers: Content-Type\r\n";
			head += "Cache-Control: no-store\r\n";
			head += "\r\n";
			return SendAll(socket, head) && SendAll(socket, response.body);
		}
	}

	// ----------------------------------------------------------------------
	// Server::Impl
	// ----------------------------------------------------------------------

	class Server::Impl
	{
	public:
		Impl(std::uint16_t port, std::shared_ptr<Backend> backend)
			: port_(port), backend_(std::move(backend))
		{
			const std::vector<DatarefInfo>& datarefs = backend_->Datarefs();
			for (std::size_t i = 0; i < datarefs.size(); ++i)
			{
				datarefIndex_.emplace(datarefs[i].id, i);
			}
			const std::vector<CommandInfo>& commands = backend_->Commands();
			for (std::size_t i = 0; i < commands.size(); ++i)
			{
				commandIndex_.emplace(commands[i].id, i);
			}
		}

		~Impl() { Stop(); }

		bool Start(std::string& error)
		{
			if (running_.load())
			{
				return true;
			}
			if (thread_.joinable())
			{
				thread_.join();
			}

			if (!wsaStarted_)
			{
				WSADATA data = {};
				const int result = WSAStartup(MAKEWORD(2, 2), &data);
				if (result != 0)
				{
					error = "WSAStartup failed with code " + std::to_string(result);
					return false;
				}
				wsaStarted_ = true;
			}

			UniqueSocket listener(socket(AF_INET, SOCK_STREAM, IPPROTO_TCP));
			if (!listener.Valid())
			{
				error = "socket() failed with code " + std::to_string(WSAGetLastError());
				CleanupWinsock();
				return false;
			}

			// Prevent another local process from hijacking the loopback port
			// with SO_REUSEADDR.
			BOOL exclusive = TRUE;
			setsockopt(
				listener.Get(),
				SOL_SOCKET,
				SO_EXCLUSIVEADDRUSE,
				reinterpret_cast<const char*>(&exclusive),
				sizeof(exclusive));

			// Reject connections from other machines; the OpenFrequency client
			// always runs on the same host as the simulator.
			sockaddr_in address = {};
			address.sin_family = AF_INET;
			address.sin_port = htons(port_);
			address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
			if (bind(
					listener.Get(),
					reinterpret_cast<const sockaddr*>(&address),
					sizeof(address)) == SOCKET_ERROR)
			{
				error = "bind(127.0.0.1:" + std::to_string(port_) +
				        ") failed with code " + std::to_string(WSAGetLastError());
				listener.Reset();
				CleanupWinsock();
				return false;
			}
			if (listen(listener.Get(), SOMAXCONN) == SOCKET_ERROR)
			{
				error = "listen() failed with code " + std::to_string(WSAGetLastError());
				listener.Reset();
				CleanupWinsock();
				return false;
			}

			u_long nonBlocking = 1;
			if (ioctlsocket(listener.Get(), FIONBIO, &nonBlocking) == SOCKET_ERROR)
			{
				error = "ioctlsocket() failed with code " + std::to_string(WSAGetLastError());
				listener.Reset();
				CleanupWinsock();
				return false;
			}

			listener_ = std::move(listener);
			stopping_.store(false);
			running_.store(true);
			try
			{
				thread_ = std::thread([this] { Run(); });
			}
			catch (const std::exception& exception)
			{
				running_.store(false);
				listener_.Reset();
				error = std::string("Failed to start HTTP thread: ") + exception.what();
				CleanupWinsock();
				return false;
			}
			return true;
		}

		void Stop()
		{
			const bool wasRunning = running_.exchange(false);
			stopping_.store(true);
			if (thread_.joinable())
			{
				thread_.join();
			}
			listener_.Reset();
			CleanupWinsock();
			if (wasRunning)
			{
				Log("Local Web API stopped");
			}
		}

		bool Running() const noexcept { return running_.load(); }

		std::vector<std::string> DrainLogs()
		{
			std::lock_guard<std::mutex> lock(logMutex_);
			std::vector<std::string> drained;
			drained.swap(logs_);
			return drained;
		}

		std::shared_ptr<Backend> GetBackend() const noexcept { return backend_; }

	private:
		void CleanupWinsock()
		{
			if (wsaStarted_)
			{
				WSACleanup();
				wsaStarted_ = false;
			}
		}

		void Log(std::string line)
		{
			std::lock_guard<std::mutex> lock(logMutex_);
			if (logs_.size() >= kMaxLogLines)
			{
				logs_.erase(logs_.begin());
			}
			logs_.push_back(std::move(line));
		}

		void Run()
		{
			while (!stopping_.load())
			{
				fd_set readSet;
				FD_ZERO(&readSet);
				FD_SET(listener_.Get(), &readSet);
				timeval timeout = {};
				timeout.tv_sec = 0;
				timeout.tv_usec = kSelectTimeoutMicroseconds;
				const int ready = select(0, &readSet, nullptr, nullptr, &timeout);
				if (ready == SOCKET_ERROR)
				{
					Log("select() failed with code " + std::to_string(WSAGetLastError()));
					break;
				}
				if (ready == 0 || !FD_ISSET(listener_.Get(), &readSet))
				{
					continue;
				}

				sockaddr_in peer = {};
				int peerLength = static_cast<int>(sizeof(peer));
				const SOCKET client = accept(
					listener_.Get(),
					reinterpret_cast<sockaddr*>(&peer),
					&peerLength);
				if (client == INVALID_SOCKET)
				{
					const int error = WSAGetLastError();
					if (error != WSAEWOULDBLOCK && error != WSAECONNRESET)
					{
						Log("accept() failed with code " + std::to_string(error));
					}
					continue;
				}
				UniqueSocket clientSocket(client);
				HandleClient(clientSocket.Get());
			}
		}

		void HandleClient(SOCKET client)
		{
			// On Windows an accepted socket can inherit the non-blocking mode of
			// the listening socket. Force blocking mode; SO_RCVTIMEO below keeps
			// a stalled peer from blocking the server forever.
			u_long blocking = 0;
			ioctlsocket(client, FIONBIO, &blocking);

			DWORD timeoutMs = kIoTimeoutMs;
			setsockopt(
				client, SOL_SOCKET, SO_RCVTIMEO,
				reinterpret_cast<const char*>(&timeoutMs), sizeof(timeoutMs));
			setsockopt(
				client, SOL_SOCKET, SO_SNDTIMEO,
				reinterpret_cast<const char*>(&timeoutMs), sizeof(timeoutMs));

			std::string buffer;
			std::array<char, 8192> chunk = {};
			while (!stopping_.load())
			{
				std::size_t headerEnd = buffer.find("\r\n\r\n");
				while (headerEnd == std::string::npos)
				{
					if (buffer.size() > kMaxHeaderBytes)
					{
						SendResponse(client, JsonError(431, "Request headers too large"), false);
						return;
					}
					const int received = recv(
						client, chunk.data(), static_cast<int>(chunk.size()), 0);
					if (received <= 0)
					{
						return;
					}
					buffer.append(chunk.data(), static_cast<std::size_t>(received));
					headerEnd = buffer.find("\r\n\r\n");
				}

				HttpRequest request;
				if (!ParseRequestHead(std::string_view(buffer).substr(0, headerEnd), request))
				{
					SendResponse(client, JsonError(400, "Malformed HTTP request"), false);
					return;
				}

				std::size_t contentLength = 0;
				const std::string_view lengthHeader = request.Header("content-length");
				if (!lengthHeader.empty())
				{
					unsigned long long parsed = 0;
					const auto [end, error] = std::from_chars(
						lengthHeader.data(),
						lengthHeader.data() + lengthHeader.size(),
						parsed);
					if (error != std::errc{} || end != lengthHeader.data() + lengthHeader.size())
					{
						SendResponse(client, JsonError(400, "Invalid Content-Length"), false);
						return;
					}
					if (parsed > kMaxBodyBytes)
					{
						SendResponse(client, JsonError(413, "Request body too large"), false);
						return;
					}
					contentLength = static_cast<std::size_t>(parsed);
				}
				if (!request.Header("transfer-encoding").empty())
				{
					SendResponse(
						client,
						JsonError(400, "Transfer-Encoding is not supported"),
						false);
					return;
				}

				const std::size_t bodyStart = headerEnd + 4;
				while (buffer.size() < bodyStart + contentLength)
				{
					const int received = recv(
						client, chunk.data(), static_cast<int>(chunk.size()), 0);
					if (received <= 0)
					{
						return;
					}
					buffer.append(chunk.data(), static_cast<std::size_t>(received));
				}
				request.body.assign(buffer, bodyStart, contentLength);
				buffer.erase(0, bodyStart + contentLength);

				const bool keepAlive = request.KeepAlive() && !stopping_.load();
				const HttpResponse response = Route(request);
				if (!SendResponse(client, response, keepAlive))
				{
					return;
				}
				if (!keepAlive)
				{
					return;
				}
			}
		}

		HttpResponse Route(const HttpRequest& request) const
		{
			const std::string path = request.Path();
			const std::string query = request.Query();

			if (request.method == "OPTIONS")
			{
				HttpResponse response;
				response.status = 204;
				return response;
			}
			if (path == "/api/capabilities")
			{
				if (request.method != "GET")
				{
					return MethodNotAllowed();
				}
				return Json(200, CapabilitiesJson());
			}
			if (path == "/api/v3/datarefs")
			{
				if (request.method != "GET")
				{
					return MethodNotAllowed();
				}
				return DatarefList(query);
			}
			if (path == "/api/v3/commands")
			{
				if (request.method != "GET")
				{
					return MethodNotAllowed();
				}
				return CommandList(query);
			}

			constexpr std::string_view datarefPrefix = "/api/v3/datarefs/";
			if (StartsWith(path, datarefPrefix))
			{
				return RouteDataref(request, std::string_view(path).substr(datarefPrefix.size()));
			}
			constexpr std::string_view commandPrefix = "/api/v3/commands/";
			if (StartsWith(path, commandPrefix))
			{
				return RouteCommand(request, std::string_view(path).substr(commandPrefix.size()));
			}
			return JsonError(404, "Not found");
		}

		HttpResponse RouteDataref(const HttpRequest& request, std::string_view rest) const
		{
			const std::size_t slash = rest.find('/');
			const std::string_view idText = slash == std::string_view::npos
				? rest
				: rest.substr(0, slash);
			const std::string_view suffix = slash == std::string_view::npos
				? std::string_view{}
				: rest.substr(slash + 1);

			int id = 0;
			if (!ParsePositiveInt(idText, id))
			{
				return JsonError(404, "Dataref not found");
			}
			const auto index = datarefIndex_.find(id);
			if (index == datarefIndex_.end())
			{
				return JsonError(404, "Dataref not found");
			}
			const DatarefInfo& info = backend_->Datarefs()[index->second];

			if (suffix.empty())
			{
				if (request.method != "GET")
				{
					return MethodNotAllowed();
				}
				const std::optional<DatarefValue> value = backend_->Read(id);
				return Json(200, DatarefObjectJson(info, value ? &*value : nullptr));
			}

			if (suffix == "value")
			{
				if (request.method == "GET")
				{
					const std::optional<DatarefValue> value = backend_->Read(id);
					if (!value)
					{
						return JsonError(503, "Dataref is not available");
					}
					return Json(200, DatarefObjectJson(info, &*value));
				}
				if (request.method == "PATCH")
				{
					return PatchDataref(info, request.body);
				}
				return MethodNotAllowed();
			}

			return JsonError(404, "Not found");
		}

		HttpResponse RouteCommand(const HttpRequest& request, std::string_view rest) const
		{
			const std::size_t slash = rest.find('/');
			const std::string_view idText = slash == std::string_view::npos
				? rest
				: rest.substr(0, slash);
			const std::string_view suffix = slash == std::string_view::npos
				? std::string_view{}
				: rest.substr(slash + 1);

			int id = 0;
			if (!ParsePositiveInt(idText, id))
			{
				return JsonError(404, "Command not found");
			}
			const auto index = commandIndex_.find(id);
			if (index == commandIndex_.end())
			{
				return JsonError(404, "Command not found");
			}
			const CommandInfo& info = backend_->Commands()[index->second];

			if (suffix.empty())
			{
				if (request.method != "GET")
				{
					return MethodNotAllowed();
				}
				return Json(200, WrapData(CommandMetadataJson(info)));
			}
			if (suffix == "activate")
			{
				if (request.method != "POST")
				{
					return MethodNotAllowed();
				}
				if (!backend_->ActivateCommand(id))
				{
					return JsonError(503, "Command is not available");
				}
				std::string object = CommandMetadataJson(info);
				object.insert(object.size() - 1, ",\"activated\":true");
				return Json(200, WrapData(std::move(object)));
			}
			return JsonError(404, "Not found");
		}

		HttpResponse PatchDataref(const DatarefInfo& info, const std::string& body) const
		{
			const std::optional<JsonNode> root = JsonParser(body).Parse();
			if (!root)
			{
				return JsonError(400, "Invalid JSON body");
			}
			if (root->kind != JsonNode::Kind::Object)
			{
				return JsonError(400, "Expected a JSON object");
			}

			const JsonNode* valueNode = nullptr;
			for (const auto& [key, value] : root->object)
			{
				if (key == "data")
				{
					valueNode = &value;
					break;
				}
			}
			if (valueNode == nullptr)
			{
				return JsonError(400, "Missing \"data\" field");
			}

			std::optional<DatarefValue> value = ToDatarefValue(*valueNode);
			if (!value)
			{
				return JsonError(400, "Unsupported value");
			}
			if (!backend_->Write(info.id, std::move(*value)))
			{
				return JsonError(404, "Dataref not found");
			}

			std::string object = DatarefMetadataJson(info);
			object.insert(object.size() - 1, ",\"ok\":true");
			return Json(200, WrapData(std::move(object)));
		}

		HttpResponse DatarefList(const std::string& query) const
		{
			const std::string filter = QueryParameter(query, "filter[name]");
			const std::vector<DatarefInfo>& datarefs = backend_->Datarefs();

			std::vector<std::size_t> matches;
			matches.reserve(datarefs.size());
			if (filter.empty())
			{
				for (std::size_t i = 0; i < datarefs.size(); ++i)
				{
					matches.push_back(i);
				}
			}
			else
			{
				// Exact matches first: OpenFrequency relies on that ordering
				// when it caches ids for the datarefs it needs.
				for (std::size_t i = 0; i < datarefs.size(); ++i)
				{
					if (datarefs[i].name == filter)
					{
						matches.push_back(i);
					}
				}
				for (std::size_t i = 0; i < datarefs.size(); ++i)
				{
					if (datarefs[i].name != filter &&
					    datarefs[i].name.find(filter) != std::string::npos)
					{
						matches.push_back(i);
					}
				}
			}

			std::string body = "{\"data\":[";
			bool first = true;
			for (const std::size_t index : matches)
			{
				if (!first)
				{
					body.push_back(',');
				}
				first = false;
				body += DatarefMetadataJson(datarefs[index]);
			}
			body += "]}";
			return Json(200, std::move(body));
		}

		HttpResponse CommandList(const std::string& query) const
		{
			const std::string filter = QueryParameter(query, "filter[name]");
			const std::vector<CommandInfo>& commands = backend_->Commands();

			std::vector<std::size_t> matches;
			matches.reserve(commands.size());
			if (filter.empty())
			{
				for (std::size_t i = 0; i < commands.size(); ++i)
				{
					matches.push_back(i);
				}
			}
			else
			{
				for (std::size_t i = 0; i < commands.size(); ++i)
				{
					if (commands[i].name == filter)
					{
						matches.push_back(i);
					}
				}
				for (std::size_t i = 0; i < commands.size(); ++i)
				{
					if (commands[i].name != filter &&
					    commands[i].name.find(filter) != std::string::npos)
					{
						matches.push_back(i);
					}
				}
			}

			std::string body = "{\"data\":[";
			bool first = true;
			for (const std::size_t index : matches)
			{
				if (!first)
				{
					body.push_back(',');
				}
				first = false;
				body += CommandMetadataJson(commands[index]);
			}
			body += "]}";
			return Json(200, std::move(body));
		}

		static std::string CapabilitiesJson()
		{
			return R"({"api":{"versions":["v3"]},"x-plane":{"version":"11.55+","web_api":"openatc-emulated"},"plugin":"OpenATC DataBridge"})";
		}

		static std::string DatarefObjectJson(
			const DatarefInfo& info,
			const DatarefValue* value)
		{
			// DatarefMetadataJson returns a complete object; reopen it so the
			// value is appended before the closing brace.
			std::string object = DatarefMetadataJson(info);
			object.pop_back();
			object += ",\"value\":";
			if (value != nullptr)
			{
				AppendJsonValue(object, *value);
			}
			else
			{
				object += "null";
			}
			object.push_back('}');
			return WrapData(std::move(object));
		}

		static std::string WrapData(std::string object)
		{
			std::string body = "{\"data\":";
			body += std::move(object);
			body.push_back('}');
			return body;
		}

		static HttpResponse Json(int status, std::string body)
		{
			HttpResponse response;
			response.status = status;
			response.body = std::move(body);
			return response;
		}

		static HttpResponse JsonError(int status, std::string_view message)
		{
			std::string body = "{\"error\":{\"code\":";
			body += std::to_string(status);
			body += ",\"message\":";
			AppendJsonString(body, message);
			body += "}}";
			return Json(status, std::move(body));
		}

		static HttpResponse MethodNotAllowed()
		{
			return JsonError(405, "Method not allowed");
		}

		std::uint16_t port_ = 0;
		std::shared_ptr<Backend> backend_;
		std::unordered_map<int, std::size_t> datarefIndex_;
		std::unordered_map<int, std::size_t> commandIndex_;

		UniqueSocket listener_;
		std::thread thread_;
		std::atomic<bool> running_{false};
		std::atomic<bool> stopping_{false};
		bool wsaStarted_ = false;

		std::mutex logMutex_;
		std::vector<std::string> logs_;
	};

	// ----------------------------------------------------------------------
	// Server public interface
	// ----------------------------------------------------------------------

	Server::Server(std::uint16_t port, std::shared_ptr<Backend> backend)
		: impl_(std::make_unique<Impl>(port, std::move(backend)))
	{
	}

	Server::~Server() = default;

	bool Server::Start(std::string& error)
	{
		return impl_->Start(error);
	}

	void Server::Stop()
	{
		impl_->Stop();
	}

	bool Server::Running() const noexcept
	{
		return impl_->Running();
	}

	std::vector<std::string> Server::DrainLogs()
	{
		return impl_->DrainLogs();
	}

	std::shared_ptr<Backend> Server::GetBackend() const noexcept
	{
		return impl_->GetBackend();
	}
}
