
JAVACFLAGS = -Xlint:all -Xlint:-serial -Werror

SOURCES := $(shell find src/ -name '*.java' 2>/dev/null)
LIBRARIES := $(shell find src/org/ 2>/dev/null)
ASSETS := $(shell find src/static/ src/pages/ 2>/dev/null)
# Specifying those explicitly as they might be absent, and then not listed
# as dependencies.
AUTOASSETS := src/static/logo-static.svg src/static/logo-static_32x32.png \
    src/static/logo-static_128x128.png src/static/logo-static_128x128.ico

_ALL_SOURCES := $(SOURCES) $(shell find tools/ -name '*.java' 2>/dev/null)
_JAVA_SOURCES := $(patsubst src/%,%,$(SOURCES))

.NOTPARALLEL:
.PHONY: clean lint lint-ro run pre-commit

Instant.jar: .build.jar $(LIBRARIES) $(ASSETS) $(AUTOASSETS)
	cp .build.jar Instant.jar
	cd src && jar uf ../Instant.jar *

all: Instant.jar
	$(MAKE) -C tools all

# Avoid recompiling the backend on frontend changes.
.SECONDARY: .build.jar
.build.jar: $(SOURCES)
	find src/net/ -name '*.class' -exec rm {} +
	cd src && javac $(JAVACFLAGS) $(_JAVA_SOURCES)
	cd src && jar cfe ../.build.jar Main $$(find . -name '*.class')

Instant-run.jar: Instant.jar tools/amend-manifest.jar
	cp Instant.jar Instant-run.jar
	java -jar tools/amend-manifest.jar Instant-run.jar X-Git-Commit \
	$$(git rev-parse HEAD)

config:
	mkdir -p $@

config/cookie-key.bin: | config
	head -c64 /dev/random > config/cookie-key.bin
	chmod 0600 config/cookie-key.bin

clean:
	rm -f .build.jar Instant.jar Instant-run.jar
	$(MAKE) -C tools clean

lint:
	script/importlint.py --sort --prune --empty-lines $(_ALL_SOURCES)
lint-ro:
	script/importlint.py $(_ALL_SOURCES)

run: Instant-run.jar config/cookie-key.bin
	cd src && INSTANT_HTTP_MAXCACHEAGE=10 \
	INSTANT_COOKIES_INSECURE=yes \
	INSTANT_COOKIES_KEYFILE=../config/cookie-key.bin \
	java -jar ../Instant-run.jar

src/static/logo-static.svg: src/static/logo.svg
	script/deanimate.py $< $@
src/static/logo-static_32x32.png: src/static/logo-static.svg
	convert -background none $< $@
# HACK: Apparently only way to make ImageMagick scale the SVG up.
src/static/logo-static_128x128.png: src/static/logo-static.svg
	convert -background none -density 288 $< $@
src/static/logo-static_128x128.ico: src/static/logo-static.svg
	convert -background none -density 288 $< $@

tools/%.jar: tools/%
	$(MAKE) -C tools $*.jar

_lint-changed:
	@script/importlint.py --sort --prune --empty-lines --report-null \
	$$(git diff --cached --name-only --diff-filter d | grep '\.java$$') \
	| xargs -r0 git add

pre-commit: _lint-changed Instant.jar
	@$(MAKE) -sC tools _pre-commit
	@git add Instant.jar
