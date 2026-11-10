# Contributor guidance

This is an MIT-licensed community fork of Mario Estrada's Antec Flux Pro display
service. Preserve LICENSE and upstream attribution in README.md, including
Nish Tahir's original Linux work and the earlier AKoskovich contribution credited
by upstream. Describe fork additions accurately.

## Testing

Run `make check`. The project uses Python's standard-library unittest runner;
the tests need neither PyUSB, root nor physical USB hardware. This also checks
Python compilation and Fedora installer shell syntax.

Arch packaging: `cd packaging/arch && makepkg --cleanbuild --noconfirm`.
Keep dependency checks and `check()` enabled. The recipe pins a source commit;
ensure that commit is available in the fork before publishing the recipe.
Regenerate `.SRCINFO` with `makepkg --printsrcinfo` after recipe changes.

Do not run USB-writing checks concurrently with the installed service, or
restart/reboot/suspend a user's machine as part of automated tests. Distinguish
mock USB tests, real USB writes, physical display confirmation and boot testing.
