import cli.Path
import cli.Env
import BuildOptions

## Bounded regular-file access. Paths are checked component by component before
## reads, copies, or directory creation; documentation never selects host paths.
BuildFiles :: [].{

	## Compare native Windows directory results with UTF-8 slash-joined roots.
	## Unix backslashes remain filename bytes and are rejected by safe_path.
	relative_path : Str, Str, Bool -> Try(Str, Str)
	relative_path = |root, child, windows| {
		base = if (windows) root.replace_each("\\", "/") else root
		text = if (windows) child.replace_each("\\", "/") else child
		prefix = "${base}/"
		if !text.starts_with(prefix) {
			return Err("filesystem returned a path outside the declared root")
		}
		path = text.drop_prefix(prefix)
		if !BuildOptions.safe_path(path) {
			return Err("unsafe relative filename")
		}
		Ok(path)
	}
	Entry : { path : Str, absolute : Path }
	inside! : Path, Str => Try(Path, Str)
	inside! = |root, relative| {
		if !BuildOptions.safe_path(relative) {
			return Err("unsafe relative path: ${relative}")
		}
		var $cursor = root
		for part in relative.split_on("/") {
			$cursor = Path.join($cursor, part)
			if Path.exists!($cursor).map_err(|e| Str.inspect(e))? {
				match Path.type!($cursor).map_err(|e| Str.inspect(e))? {
					IsSymLink => return Err("symlink not accepted: ${relative}")
					IsOther => return Err("special file not accepted: ${relative}")
					_ => {}
				}
			}
		}
		Ok($cursor)
	}
	read! : Path, U64 => Try(List(U8), Str)
	read! = |path, limit| {
		if !Path.is_file!(path).map_err(|e| Str.inspect(e))? {
			return Err("input must be a regular file: ${Path.display(path)}")
		}
		if Path.size_in_bytes!(path).map_err(|e| Str.inspect(e))? > limit {
			return Err("input exceeds byte limit: ${Path.display(path)}")
		}
		bytes = Path.read_bytes!(path).map_err(|e| Str.inspect(e))?
		if bytes.len() > limit {
			return Err("input changed beyond byte limit")
		}
		Ok(bytes)
	}
	list! : Path => Try(List(Entry), Str)
	list! = |root| walk!(root, root, 0, [])
	copy! : Path, Path => Try({}, Str)
	copy! = |source, target| {
		if !Path.is_file!(source).map_err(|e| Str.inspect(e))? {
			return Err("copy input must remain a regular file")
		}
		parent = parent_of(target)
		Path.create_all!(parent).map_err(|e| Str.inspect(e))?
		Path.copy!(source, target).map_err(|e| Str.inspect(e))
	}
	parent_path = |path| parent_of(path)
	write_text! = |root, relative, source| {
		path = inside!(root, relative)?
		Path.create_all!(parent_of(path)).map_err(|e| Str.inspect(e))?
		Path.write_utf8!(path, source).map_err(|e| Str.inspect(e))
	}
	write_bytes! = |root, relative, bytes| {
		path = inside!(root, relative)?
		Path.create_all!(parent_of(path)).map_err(|e| Str.inspect(e))?
		Path.write_bytes!(path, bytes).map_err(|e| Str.inspect(e))
	}
}

parent_of = |path| Path.utf8(Str.join_with(Path.display(path).split_on("/").drop_last(1), "/"))

walk! : Path, Path, U64, List(BuildFiles.Entry) => Try(List(BuildFiles.Entry), Str)
walk! = |root, current, depth, found| {
	if depth > 32 {
		return Err("documentation directory nesting exceeds limit")
	}
	var $files = found
	children = Path.list!(current).map_err(|e| Str.inspect(e))?
	if children.len() + $files.len() > 8192 {
		return Err("documentation file count exceeds limit")
	}
	for child in children {
		text = Path.to_str(child).map_err(|_| "documentation paths must be valid UTF-8")?
		windows = match Env.platform!().os {
			WINDOWS => Bool.True
			_ => Bool.False
		}
		relative = BuildFiles.relative_path(Path.display(root), text, windows)?
		match Path.type!(child).map_err(|e| Str.inspect(e))? {
			IsFile => {
				$files = $files.append({ path: relative, absolute: child })
			}
			IsDir => {
				$files = walk!(root, child, depth + 1, $files)?
			}
			_ => return Err("documentation tree contains a symlink or special file: ${relative}")
		}
		if $files.len() > 4096 {
			return Err("documentation file count exceeds limit")
		}
	}
	Ok($files.sort_with(|a, b| compare_text(a.path, b.path)))
}

compare_text = |a, b| {
	left = a.to_utf8()
	right = b.to_utf8()
	for i in 0..<(if (left.len() < right.len()) left.len() else right.len()) {
		l = left.get(i) ?? 0
		r = right.get(i) ?? 0
		if l < r {
			return Before
		}
		if l > r {
			return After
		}
	}
	if (left.len() < right.len()) Before else if (left.len() > right.len()) After else Same
}

expect BuildFiles.relative_path("C:\\work\\tree/examples", "C:\\work\\tree\\examples\\sub\\main.roc", Bool.True) == Ok("sub/main.roc")
expect BuildFiles.relative_path("/work/tree/examples", "/work/tree/examples/sub/main.roc", Bool.False) == Ok("sub/main.roc")
expect BuildFiles.relative_path("/work/tree/examples", "/work/tree/examples/sub\\main.roc", Bool.False).is_err()
expect BuildFiles.relative_path("C:\\work\\tree/examples", "C:\\work\\other\\main.roc", Bool.True).is_err()
