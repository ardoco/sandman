## Architecture

The system has two components. The first stores media.

### Components

- The Cache keeps hot items.
- The Store persists all items.

### Interfaces

| Name        | Provider   |
|-------------|------------|
| IMediaStore | Store      |

Clients use the interface. It is stable.