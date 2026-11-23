using System;
using System.Collections;
using System.Collections.Generic;
using System.Dynamic;
using System.Linq;
using System.Reflection;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

public static class DynamicPrettyPrinter
{
    private const string Indent = "    ";
    private static HashSet<object> _visitedObjects = new(new ReferenceEqualityComparer());

    /// <summary>
    /// Pretty prints a JToken or other object with indentation
    /// </summary>
    public static string Print(object obj, int indentLevel = 0, bool showValues = true)
    {
        if (obj is null)
            return "null";

        if (obj is string)
        {
            obj = JToken.Parse(obj.ToString());
        }

        // Reset visited objects when starting from the root
        if (indentLevel == 0)
            _visitedObjects = new HashSet<object>(new ReferenceEqualityComparer());

        // Check for circular references
        if (!obj.GetType().IsPrimitive)
        {
            if (_visitedObjects.Contains(obj))
                return "[Circular Reference]";

            _visitedObjects.Add(obj);
        }

        var builder = new StringBuilder();
        var indentation = string.Concat(Enumerable.Repeat(Indent, indentLevel));
        var objType = obj.GetType();

        // Handle JSON.NET JToken types
        if (obj is JToken jToken)
        {
            switch (jToken.Type)
            {
                case JTokenType.Object:
                    var jObject = (JObject)jToken;
                    builder.AppendLine("{");
                    var first = true;

                    foreach (var property in jObject.Properties())
                    {
                        if (!first)
                            builder.AppendLine(",");

                        first = false;

                        builder.Append(indentation + Indent);
                        builder.Append($"\"{property.Name}\": ");

                        // For objects and arrays, process them normally
                        if (property.Value is JObject or JArray)
                        {
                            builder.Append(Print(property.Value, indentLevel + 1, showValues));
                        }
                        // For primitive values, hide them if showValues is false
                        else if (!showValues)
                        {
                            builder.Append("\"...\"");
                        }
                        else
                        {
                            builder.Append(Print(property.Value, indentLevel + 1));
                        }
                    }

                    if (!first) builder.AppendLine();
                    builder.Append(indentation + "}");
                    return builder.ToString();

                case JTokenType.Array:
                    var jArray = (JArray)jToken;
                    builder.AppendLine("[");
                    var arrayFirst = true;

                    foreach (var item in jArray)
                    {
                        if (!arrayFirst) builder.AppendLine(",");
                        arrayFirst = false;

                        builder.Append(indentation + Indent);

                        // For objects and arrays, process them normally
                        if (item is JObject || item is JArray)
                        {
                            builder.Append(Print(item, indentLevel + 1, showValues));
                        }
                        // For primitive values, hide them if showValues is false
                        else if (!showValues)
                        {
                            builder.Append("\"...\"");
                        }
                        else
                        {
                            builder.Append(Print(item, indentLevel + 1));
                        }
                    }

                    if (!arrayFirst) builder.AppendLine();
                    builder.Append(indentation + "]");
                    return builder.ToString();

                case JTokenType.String:
                    return showValues ? $"\"{jToken.Value<string>()}\"" : "\"...\"";

                case JTokenType.Integer:
                case JTokenType.Float:
                case JTokenType.Boolean:
                case JTokenType.Date:
                    return showValues ? jToken.ToString(Formatting.None) : "\"...\"";

                case JTokenType.Null:
                    return "null";

                default:
                    return showValues ? jToken.ToString(Formatting.None) : "\"...\"";
            }
        }

        // Handle primitive types and strings
        if (objType.IsPrimitive || objType == typeof(string) || objType == typeof(decimal) ||
            objType == typeof(DateTime) || objType == typeof(DateTimeOffset) ||
            objType == typeof(TimeSpan) || objType == typeof(Guid))
        {
            if (!showValues)
                return "\"...\"";

            if (objType == typeof(string))
                return $"\"{obj}\"";
            return obj.ToString();
        }

        switch (obj)
        {
            // Handle arrays and lists
            case IEnumerable enumerable and not IDictionary and not ExpandoObject:
            {
                builder.AppendLine("[");
                var first = true;

                foreach (var item in enumerable)
                {
                    if (!first) builder.AppendLine(",");
                    first = false;

                    builder.Append(indentation + Indent);

                    // For objects, process them normally regardless of showValues
                    if (item != null && (item.GetType().IsClass &&
                                         !(item.GetType().IsPrimitive || item is string || item is decimal ||
                                           item is DateTime || item is DateTimeOffset ||
                                           item is TimeSpan || item is Guid)))
                    {
                        builder.Append(Print(item, indentLevel + 1, showValues));
                    }
                    // For primitive values, hide them if showValues is false
                    else if (!showValues)
                    {
                        builder.Append("\"...\"");
                    }
                    else
                    {
                        builder.Append(Print(item, indentLevel + 1));
                    }
                }

                if (!first) builder.AppendLine();
                builder.Append(indentation + "]");
                return builder.ToString();
            }
            // Handle dictionaries
            case IDictionary dictionary:
            {
                builder.AppendLine("{");
                var first = true;

                foreach (DictionaryEntry entry in dictionary)
                {
                    if (!first) builder.AppendLine(",");
                    first = false;

                    builder.Append(indentation + Indent);
                    builder.Append($"\"{entry.Key}\": ");

                    // For objects and collections, process them normally
                    if (entry.Value is IEnumerable and not string)
                    {
                        builder.Append(Print(entry.Value, indentLevel + 1, showValues));
                    }
                    // For primitive values, hide them if showValues is false
                    else if (!showValues)
                    {
                        builder.Append("\"...\"");
                    }
                    else
                    {
                        builder.Append(Print(entry.Value, indentLevel + 1));
                    }
                }

                if (!first) builder.AppendLine();
                builder.Append(indentation + "}");
                return builder.ToString();
            }
            // Handle ExpandoObject specifically
            case ExpandoObject expando:
            {
                // ExpandoObject implements IDictionary<string, object>
                IDictionary<string, object> expandoDictionary = expando;
                builder.AppendLine("{");
                var first = true;

                foreach (var entry in expandoDictionary)
                {
                    if (!first) builder.AppendLine(",");
                    first = false;

                    builder.Append(indentation + Indent);
                    builder.Append($"\"{entry.Key}\": ");

                    // For objects and collections, process them normally
                    if (entry.Value != null && ((entry.Value is IEnumerable && !(entry.Value is string)) ||
                                                entry.Value.GetType().IsClass))
                    {
                        builder.Append(Print(entry.Value, indentLevel + 1, showValues));
                    }
                    // For primitive values, hide them if showValues is false
                    else if (!showValues)
                    {
                        builder.Append("\"...\"");
                    }
                    else
                    {
                        builder.Append(Print(entry.Value, indentLevel + 1));
                    }
                }

                if (!first) builder.AppendLine();
                builder.Append(indentation + "}");
                return builder.ToString();
            }
        }

        // Handle DynamicObject (removed dynamic keyword usage)
        PropertyInfo[] properties;
        if (obj is DynamicObject)
        {
            builder.AppendLine("{");

            // Use reflection to get properties of DynamicObject
            properties = objType.GetProperties(BindingFlags.Public | BindingFlags.Instance);
            var first = true;

            foreach (var prop in properties)
            {
                // Skip indexers and properties without getters
                if (prop.GetIndexParameters().Length > 0 || !prop.CanRead)
                    continue;

                if (!first) builder.AppendLine(",");
                first = false;

                builder.Append(indentation + Indent);
                builder.Append($"\"{prop.Name}\": ");

                try
                {
                    var value = prop.GetValue(obj);

                    // For objects and collections, process them normally
                    if (value != null && ((value is IEnumerable && !(value is string)) ||
                                          value.GetType().IsClass))
                    {
                        builder.Append(Print(value, indentLevel + 1, showValues));
                    }
                    // For primitive values, hide them if showValues is false
                    else if (!showValues)
                    {
                        builder.Append("\"...\"");
                    }
                    else
                    {
                        builder.Append(Print(value, indentLevel + 1));
                    }
                }
                catch
                {
                    builder.Append("[Error accessing property]");
                }
            }

            if (!first) builder.AppendLine();
            builder.Append(indentation + "}");
            return builder.ToString();
        }

        // Handle regular objects
        builder.AppendLine("{");
        properties = objType.GetProperties(BindingFlags.Public | BindingFlags.Instance);
        var objFirst = true;

        foreach (var prop in properties)
        {
            // Skip indexers and properties without getters
            if (prop.GetIndexParameters().Length > 0 || !prop.CanRead)
                continue;

            if (!objFirst) builder.AppendLine(",");
            objFirst = false;

            builder.Append(indentation + Indent);
            builder.Append($"\"{prop.Name}\": ");

            try
            {
                var value = prop.GetValue(obj);

                // For objects and collections, process them normally
                if (value != null && ((value is IEnumerable && !(value is string)) ||
                                      (value.GetType().IsClass &&
                                       !(value.GetType().IsPrimitive || value is string || value is decimal ||
                                         value is DateTime || value is DateTimeOffset ||
                                         value is TimeSpan || value is Guid))))
                {
                    builder.Append(Print(value, indentLevel + 1, showValues));
                }
                // For primitive values, hide them if showValues is false
                else if (!showValues)
                {
                    builder.Append("\"...\"");
                }
                else
                {
                    builder.Append(Print(value, indentLevel + 1, showValues));
                }
            }
            catch
            {
                builder.Append("[Error accessing property]");
            }
        }

        if (!objFirst) builder.AppendLine();
        builder.Append(indentation + "}");
        return builder.ToString();
    }
}

/// <summary>
/// Custom equality comparer for reference equality checks
/// </summary>
internal class ReferenceEqualityComparer : IEqualityComparer<object>
{
    public new bool Equals(object x, object y)
    {
        return ReferenceEquals(x, y);
    }

    public int GetHashCode(object obj)
    {
        return System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(obj);
    }
}